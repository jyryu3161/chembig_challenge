from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('arena', '0002_submission_quota_exempt'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='val_metrics',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='submission',
            name='test_metrics',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='problem',
            name='kind',
            field=models.CharField(choices=[('regression', '회귀'), ('binary', '이진분류')], max_length=16, verbose_name='유형'),
        ),
        migrations.AlterField(
            model_name='problem',
            name='metric',
            field=models.CharField(
                choices=[('rmse', 'RMSE'), ('mae', 'MAE'), ('mse', 'MSE'), ('r2', 'R²'), ('pearson', 'Pearson r'), ('spearman', 'Spearman ρ'),
                         ('roc_auc', 'ROC-AUC'), ('ap', 'AUPRC (Average Precision)'), ('log_loss', 'Log Loss'), ('accuracy', 'Accuracy'),
                         ('balanced_accuracy', 'Balanced Accuracy'), ('f1', 'F1'), ('mcc', 'MCC'), ('precision', 'Precision'),
                         ('recall', 'Recall (Sensitivity)'), ('specificity', 'Specificity')],
                default='rmse', max_length=24, verbose_name='순위 지표',
                help_text='순위를 결정하는 주 지표입니다. 같은 유형의 나머지 지표는 참고용으로 함께 계산됩니다.'),
        ),
    ]
