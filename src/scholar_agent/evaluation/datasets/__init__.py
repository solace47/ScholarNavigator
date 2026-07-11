"""公开学术检索 Benchmark 的统一数据集适配入口。"""

from scholar_agent.evaluation.datasets.registry import (
    BenchmarkDatasetReport,
    dataset_source_path,
    inspect_dataset,
    load_dataset,
    supported_datasets,
)

__all__ = [
    "BenchmarkDatasetReport",
    "dataset_source_path",
    "inspect_dataset",
    "load_dataset",
    "supported_datasets",
]
