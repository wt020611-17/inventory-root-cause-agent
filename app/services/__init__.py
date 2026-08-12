"""应用服务层公共入口：提供不依赖 HTTP 或 LLM 的确定性业务用例。"""

from app.services.inventory_analysis import InventoryAnalysisService

__all__ = ["InventoryAnalysisService"]
