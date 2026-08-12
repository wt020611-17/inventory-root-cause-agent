"""纯合成数据包：生成可复现业务事实并执行整批数据质量检查。"""

from app.synthetic.generator import SyntheticDataset, generate_synthetic_dataset
from app.synthetic.quality import DataQualityReport, validate_synthetic_dataset

__all__ = [
    "DataQualityReport",
    "SyntheticDataset",
    "generate_synthetic_dataset",
    "validate_synthetic_dataset",
]
