#!/usr/bin/env python
"""Export a TDC ADMET benchmark task as ChemBIG train/val/test CSVs plus a create_contest spec skeleton.

Runs outside the Docker image (PyTDC is not a platform dependency):
    uv venv -p 3.12 /tmp/tdcenv && uv pip install -p /tmp/tdcenv/bin/python pandas numpy tqdm requests \
        fuzzywuzzy scikit-learn seaborn rdkit huggingface_hub "setuptools<81"
    uv pip install -p /tmp/tdcenv/bin/python --no-deps "PyTDC==0.4.1"
    /tmp/tdcenv/bin/python scripts/fetch_tdc.py BBB_Martins --out var/imports/bbbp --seed 1

The ADMET benchmark group fixes the scaffold-split test set; train/valid is the group's seeded split.
Rows are shuffled with a fixed seed and given opaque IDs so that row order and IDs carry no label information.
"""
import argparse
import json
import random
from pathlib import Path

import pandas as pd

TASKS = {  # TDC ADMET group name → (kind, TDC primary metric, ChemBIG metric key, Korean title, unit/description)
    'BBB_Martins': ('binary', 'AUROC', 'roc_auc', '혈액뇌장벽(BBB) 투과 예측 · BBBP', '투과(1) / 비투과(0) 확률'),
    'Caco2_Wang': ('regression', 'MAE', 'mae', 'Caco-2 세포 투과도 예측', 'log(cm/s)'),
    'Lipophilicity_AstraZeneca': ('regression', 'MAE', 'mae', '지용성(logD) 예측', 'logD at pH 7.4'),
    'Solubility_AqSolDB': ('regression', 'MAE', 'mae', '수용해도 예측 · AqSolDB', 'log mol/L'),
    'HIA_Hou': ('binary', 'AUROC', 'roc_auc', '인체 장 흡수(HIA) 예측', '흡수(1) 확률'),
    'Pgp_Broccatelli': ('binary', 'AUROC', 'roc_auc', 'P-glycoprotein 억제 예측', '억제(1) 확률'),
    'Bioavailability_Ma': ('binary', 'AUROC', 'roc_auc', '경구 생체이용률 예측', '≥20%(1) 확률'),
    'PPBR_AZ': ('regression', 'MAE', 'mae', '혈장 단백 결합률 예측', '% bound'),
    'VDss_Lombardo': ('regression', 'Spearman', 'spearman', '분포 용적(VDss) 예측', 'L/kg (log)'),
    'CYP2C9_Veith': ('binary', 'AUPRC', 'ap', 'CYP2C9 억제 예측', '억제(1) 확률'),
    'CYP2D6_Veith': ('binary', 'AUPRC', 'ap', 'CYP2D6 억제 예측', '억제(1) 확률'),
    'CYP3A4_Veith': ('binary', 'AUPRC', 'ap', 'CYP3A4 억제 예측', '억제(1) 확률'),
    'CYP2C9_Substrate_CarbonMangels': ('binary', 'AUPRC', 'ap', 'CYP2C9 기질 예측', '기질(1) 확률'),
    'CYP2D6_Substrate_CarbonMangels': ('binary', 'AUPRC', 'ap', 'CYP2D6 기질 예측', '기질(1) 확률'),
    'CYP3A4_Substrate_CarbonMangels': ('binary', 'AUROC', 'roc_auc', 'CYP3A4 기질 예측', '기질(1) 확률'),
    'Half_Life_Obach': ('regression', 'Spearman', 'spearman', '반감기 예측', 'hours'),
    'Clearance_Hepatocyte_AZ': ('regression', 'Spearman', 'spearman', '간세포 청소율 예측', 'μL/min/10^6 cells'),
    'Clearance_Microsome_AZ': ('regression', 'Spearman', 'spearman', '마이크로솜 청소율 예측', 'mL/min/g'),
    'LD50_Zhu': ('regression', 'MAE', 'mae', '급성 독성 LD50 예측', '-log(mol/kg)'),
    'hERG': ('binary', 'AUROC', 'roc_auc', 'hERG 차단 예측', '차단(1) 확률'),
    'AMES': ('binary', 'AUROC', 'roc_auc', 'Ames 돌연변이원성 예측', '양성(1) 확률'),
    'DILI': ('binary', 'AUROC', 'roc_auc', '약물 유발 간 손상(DILI) 예측', '양성(1) 확률'),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('name', choices=sorted(TASKS), help='TDC ADMET benchmark task name')
    ap.add_argument('--out', required=True, help='output directory for train/val/test CSV and spec.json')
    ap.add_argument('--seed', type=int, default=1, help='TDC train/valid split seed (benchmark uses 1-5)')
    ap.add_argument('--cache', default='tdc_data', help='TDC download cache directory')
    args = ap.parse_args()

    from tdc.benchmark_group import admet_group
    group = admet_group(path=args.cache)
    test = group.get(args.name)['test']
    train, valid = group.get_train_valid_split(benchmark=args.name, split_type='default', seed=args.seed)
    kind, tdc_metric, metric, title, units = TASKS[args.name]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(2026)
    for split, df in (('train', train), ('val', valid), ('test', test)):
        df = df.reset_index(drop=True)
        order = list(range(len(df)))
        rng.shuffle(order)
        df = df.iloc[order].reset_index(drop=True)
        width = len(str(len(df)))
        frame = pd.DataFrame({
            'sample_id': [f'{split}_{i + 1:0{width}d}' for i in range(len(df))],
            'SMILES': df['Drug'].astype(str),
            'target': df['Y'].astype(int) if kind == 'binary' else df['Y'].astype(float),
        })
        frame.to_csv(out / f'{split}.csv', index=False, lineterminator='\n')
        print(f'{split}: {len(frame)} rows', frame['target'].value_counts().to_dict() if kind == 'binary' else '')

    spec = {
        'semester': {'year': 2026, 'term': '2학기'},
        'contest': {
            'title': f'TDC ADMET 벤치마크 · {args.name}',
            'description': f'Therapeutics Data Commons(TDC) ADMET 벤치마크의 {args.name} 과제입니다.',
            'rules': '개인전 · 외부 데이터 사용 여부는 교수자의 안내를 따릅니다.',
            'opens_at': '2026-09-21T18:00:00+09:00', 'closes_at': '2026-12-04T23:59:59+09:00',
            'daily_limit': 5, 'invite_code': 'CHANGE-ME', 'visible': True,
        },
        'problems': [{
            'title': title, 'kind': kind, 'metric': metric,
            'description': f'TDC {args.name}. 벤치마크 공식 지표는 {tdc_metric}입니다.',
            'units': units,
            'source': f'Therapeutics Data Commons ADMET Benchmark Group · {args.name} · https://tdcommons.ai/benchmark/admet_group/',
            'split_method': f'TDC 벤치마크 scaffold split (test 고정) · train/valid는 TDC 기본 분할 seed {args.seed}',
            'id_column': 'sample_id', 'smiles_column': 'SMILES', 'target_column': 'target', 'feature_columns': [],
            'files': {'train': 'train.csv', 'val': 'val.csv', 'test': 'test.csv'}, 'publish': True,
        }],
    }
    (out / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {out}/spec.json — edit invite_code, dates and texts, then: manage.py create_contest {out}/spec.json')


if __name__ == '__main__':
    main()
