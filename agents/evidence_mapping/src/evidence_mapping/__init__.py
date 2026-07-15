"""模块 4：证据梳理 Agent。

职责：将候选假设与支持 / 反对 / 不确定证据绑定，形成可追踪证据链，并输出质量评审。
"""

from .agent import EvidenceMappingAgent
from .models import AgentResponse, EvidenceMapPayload

__all__ = ["EvidenceMappingAgent", "AgentResponse", "EvidenceMapPayload"]
__version__ = "0.1.0"