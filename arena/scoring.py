"""Pure CSV validation. IDs are strings; no pandas inference or executable uploads."""
import csv
import io
import math
import numpy as np
from sklearn.metrics import mean_absolute_error, roc_auc_score, average_precision_score
from django.core.exceptions import ValidationError


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
    datasets, seen, warnings, smiles = {}, set(), [], set()
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
        current = {r[problem.smiles_column].strip() for r in rows}
        if '' in current: raise ValidationError(f'{split}: SMILES가 비어 있습니다.')
        if smiles.intersection(current):
            warnings.append(f'{split}: 다른 분할과 동일한 SMILES가 있습니다. 분할 방법을 확인하세요.')
        smiles.update(current)
        datasets[split] = rows
    return datasets, warnings


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


def score(metric, truth, predictions):
    with np.errstate(over='raise', invalid='raise'):
        y, p = np.asarray(truth, dtype=float), np.asarray(predictions, dtype=float)
        if metric == 'rmse': value = float(np.sqrt(np.mean(np.square(y-p))))
        elif metric == 'mae': value = float(mean_absolute_error(y, p))
        elif metric == 'roc_auc': value = float(roc_auc_score(y, p))
        elif metric == 'ap': value = float(average_precision_score(y, p))
        else: raise ValueError('Unknown metric')
    if not math.isfinite(value): raise ValidationError('점수를 계산할 수 없는 수치 범위입니다.')
    return value


def csv_bytes(columns, rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8')
