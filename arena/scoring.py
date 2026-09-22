"""Pure CSV validation and metric registry. IDs are strings; no pandas inference or executable uploads."""
import csv
import io
import logging
import math
from dataclasses import dataclass
from typing import Callable
import numpy as np
from scipy.stats import spearmanr
from sklearn import metrics as skm
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
# Binary label metrics (accuracy, F1, ...) threshold the submitted positive-class probability here.
BINARY_THRESHOLD = 0.5
_EPS = 1e-15


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    kind: str          # 'regression' | 'binary'
    minimize: bool
    fn: Callable[[np.ndarray, np.ndarray], float]
    note: str = ''


def _labels(p):
    return (p >= BINARY_THRESHOLD).astype(int)


class MetricUndefined(ValueError):
    """The metric has no value for this input (constant predictions for a correlation, overflow, NaN).

    Only this exception is treated as an invalid submission; any other error from a metric function is a bug and
    must surface as a logged server error, never as a message blaming the student's file."""


CONSTANT = '예측값이 모두 같으면 계산할 수 없습니다.'
RANGE = '예측값의 수치 범위를 확인하세요.'


def _constant(*arrays):
    # min == max needs no arithmetic; np.std/np.ptp square or subtract and overflow for large finite values.
    return any(a.min() == a.max() for a in arrays)


def _pearson(y, p):
    if _constant(y, p): raise MetricUndefined(CONSTANT)
    return float(np.corrcoef(y, p)[0, 1])


def _spearman(y, p):
    if _constant(y, p): raise MetricUndefined(CONSTANT)
    return float(spearmanr(y, p).statistic)


def _log_loss(y, p):
    return float(skm.log_loss(y, np.clip(p, _EPS, 1 - _EPS), labels=[0, 1]))


REGISTRY = {m.key: m for m in [
    # Regression: the TDC ADMET benchmark reports MAE and Spearman; RMSE/MSE/R²/Pearson are common course metrics.
    Metric('rmse', 'RMSE', 'regression', True, lambda y, p: float(np.sqrt(np.mean(np.square(y - p))))),
    Metric('mae', 'MAE', 'regression', True, lambda y, p: float(skm.mean_absolute_error(y, p))),
    Metric('mse', 'MSE', 'regression', True, lambda y, p: float(np.mean(np.square(y - p)))),
    Metric('r2', 'R²', 'regression', False, lambda y, p: float(skm.r2_score(y, p))),
    Metric('pearson', 'Pearson r', 'regression', False, _pearson, CONSTANT),
    Metric('spearman', 'Spearman ρ', 'regression', False, _spearman, CONSTANT),
    # Binary classification: ranking metrics use the raw probability; label metrics threshold at 0.5.
    Metric('roc_auc', 'ROC-AUC', 'binary', False, lambda y, p: float(skm.roc_auc_score(y, p))),
    Metric('ap', 'AUPRC (Average Precision)', 'binary', False, lambda y, p: float(skm.average_precision_score(y, p))),
    Metric('log_loss', 'Log Loss', 'binary', True, _log_loss),
    Metric('accuracy', 'Accuracy', 'binary', False, lambda y, p: float(skm.accuracy_score(y, _labels(p))), '임계값 0.5'),
    Metric('balanced_accuracy', 'Balanced Accuracy', 'binary', False, lambda y, p: float(skm.balanced_accuracy_score(y, _labels(p))), '임계값 0.5'),
    Metric('f1', 'F1', 'binary', False, lambda y, p: float(skm.f1_score(y, _labels(p), zero_division=0)), '임계값 0.5'),
    Metric('mcc', 'MCC', 'binary', False, lambda y, p: float(skm.matthews_corrcoef(y, _labels(p))), '임계값 0.5'),
    Metric('precision', 'Precision', 'binary', False, lambda y, p: float(skm.precision_score(y, _labels(p), zero_division=0)), '임계값 0.5'),
    Metric('recall', 'Recall (Sensitivity)', 'binary', False, lambda y, p: float(skm.recall_score(y, _labels(p), zero_division=0)), '임계값 0.5'),
    Metric('specificity', 'Specificity', 'binary', False, lambda y, p: float(skm.recall_score(y, _labels(p), pos_label=0, zero_division=0)), '임계값 0.5'),
]}

KINDS = [('regression', '회귀'), ('binary', '이진분류')]
METRIC_CHOICES = [(m.key, m.label) for m in REGISTRY.values()]


def metrics_for(kind):
    return [m for m in REGISTRY.values() if m.kind == kind]


def metric_kind(key):
    return REGISTRY[key].kind


def read_csv(raw):
    try:
        text = raw.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader)
        if not header or len(set(header)) != len(header) or any(not x for x in header):
            raise ValidationError('CSV 열 이름이 비어 있거나 중복되었습니다.')
        rows = []
        for n, row in enumerate(reader, 2):
            if len(row) != len(header):
                raise ValidationError(f'{n}행의 열 수가 올바르지 않습니다.')
            rows.append(dict(zip(header, row)))
        if not rows:
            raise ValidationError('CSV에 데이터 행이 없습니다.')
        return header, rows
    except (UnicodeError, csv.Error, StopIteration):
        raise ValidationError('UTF-8 형식의 올바른 CSV 파일을 업로드하세요.')


def number(value):
    try:
        x = float(value)
        if not math.isfinite(x): raise ValueError
        return x
    except (ValueError, TypeError, OverflowError):
        raise ValidationError('예측값·정답은 비어 있지 않은 유한한 숫자여야 합니다. NaN·Infinity는 사용할 수 없습니다.')


def validate_datasets(problem, files):
    datasets, seen, warnings_, smiles = {}, set(), [], set()
    required = [problem.id_column, problem.smiles_column, problem.target_column, *problem.feature_columns]
    for split in ('train', 'val', 'test'):
        header, rows = read_csv(files[split])
        if not set(required).issubset(header):
            raise ValidationError(f'{split}: 지정된 ID·SMILES·정답·특징 열이 없습니다.')
        ids = [r[problem.id_column] for r in rows]
        if any(not i.strip() for i in ids) or len(set(ids)) != len(ids):
            raise ValidationError(f'{split}: ID 누락 또는 중복입니다.')
        if seen.intersection(ids):
            raise ValidationError('train·val·test 사이에 겹치는 ID가 있습니다.')
        seen.update(ids)
        values = [number(r[problem.target_column]) for r in rows]
        if problem.kind == 'binary' and set(values) != {0.0, 1.0}:
            raise ValidationError(f'{split}: 이진분류 정답은 0과 1 두 클래스가 모두 있어야 합니다.')
        if problem.kind == 'regression' and len(set(values)) < 2:
            raise ValidationError(f'{split}: 회귀 정답이 모두 같은 값이면 지표를 계산할 수 없습니다.')
        current = {r[problem.smiles_column].strip() for r in rows}
        if '' in current: raise ValidationError(f'{split}: SMILES가 비어 있습니다.')
        if smiles.intersection(current):
            warnings_.append(f'{split}: 다른 분할과 동일한 SMILES가 있습니다. 분할 방법을 확인하세요.')
        smiles.update(current)
        datasets[split] = rows
    return datasets, warnings_


def validate_predictions(problem, raw, expected_ids):
    header, rows = read_csv(raw)
    if header != ['sample_id', 'prediction']:
        raise ValidationError('CSV 열은 sample_id,prediction 순서의 두 열이어야 합니다.')
    ids = [r['sample_id'] for r in rows]
    if len(set(ids)) != len(ids): raise ValidationError('중복된 sample_id가 있습니다.')
    if set(ids) != set(expected_ids):
        raise ValidationError(f'ID가 일치하지 않습니다. 누락 {len(set(expected_ids)-set(ids))}개, 추가 {len(set(ids)-set(expected_ids))}개. val·test 전체를 포함하세요.')
    result = {r['sample_id']: number(r['prediction']) for r in rows}
    if problem.kind == 'binary' and any(x < 0 or x > 1 for x in result.values()):
        raise ValidationError('이진분류 예측 확률은 0 이상 1 이하여야 합니다.')
    return result


def _compute(m, y, p):
    """Finite value of one metric. Raises MetricUndefined for undefined values; other exceptions propagate.

    No warnings.catch_warnings() here: it swaps process-global state and gunicorn runs threads, so a concurrent call
    could leave the filter list permanently altered. numpy's errstate is thread-local and turns silent overflow
    or NaN into exceptions instead."""
    try:
        with np.errstate(over='raise', invalid='raise'):
            value = m.fn(y, p)
    except (FloatingPointError, OverflowError) as e:
        raise MetricUndefined(RANGE) from e
    if not math.isfinite(value): raise MetricUndefined(RANGE)
    return value


def _undefined(m, e):
    return ValidationError(f'{m.label} 점수를 계산할 수 없습니다. {e}')


def score(metric, truth, predictions):
    """Primary metric. Raises ValidationError when the value is undefined (overflow, constant predictions)."""
    if metric not in REGISTRY: raise ValueError('Unknown metric')
    m = REGISTRY[metric]
    try:
        return _compute(m, np.asarray(truth, dtype=float), np.asarray(predictions, dtype=float))
    except MetricUndefined as e:
        raise _undefined(m, e)


def score_all(kind, truth, predictions, primary=None):
    """Every metric of the kind. The `primary` (ranking) metric must succeed: undefined → ValidationError, any other
    error propagates as a server error. A reference-only metric becomes None instead: silently when undefined,
    with a logged traceback when it fails for another reason, so one broken secondary never blocks a submission."""
    y, p = np.asarray(truth, dtype=float), np.asarray(predictions, dtype=float)
    result = {}
    for m in metrics_for(kind):
        try:
            result[m.key] = _compute(m, y, p)
        except MetricUndefined as e:
            if m.key == primary: raise _undefined(m, e)
            result[m.key] = None
        except Exception:
            if m.key == primary: raise
            logger.exception('Secondary metric %s failed; stored as None', m.key)
            result[m.key] = None
    return result


def csv_bytes(columns, rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8')
