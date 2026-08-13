"""从业务外键即时派生受限 NetworkX 证据图。"""

from datetime import date
from time import monotonic

import networkx as nx

from app.domain.enums import EvidenceNodeType, EvidenceRelationType, ResultStatus
from app.domain.results import (
    EvidenceGraphEdge,
    EvidenceGraphNode,
    EvidenceGraphResult,
    EvidencePath,
    ResultMetadata,
)
from app.repositories.protocols import InventoryRepository


class EvidenceGraphService:
    """构图并查询物料关系路径，不维护额外关系表。"""

    def __init__(self, repository: InventoryRepository) -> None:
        self._repository = repository

    def trace(
        self,
        *,
        material_id: str,
        warehouse_id: str,
        as_of_date: date,
        trace_id: str,
        max_hops: int = 2,
        max_nodes: int = 50,
        timeout_ms: int = 100,
    ) -> EvidenceGraphResult:
        """返回与目标物料相连的路径；限制超出时安全阻断。"""
        metadata_values = {"trace_id": trace_id, "as_of_date": as_of_date}
        try:
            material = self._repository.get_material(material_id)
            warehouse = self._repository.get_warehouse(warehouse_id)
            if material is None or warehouse is None:
                return EvidenceGraphResult(
                    metadata=ResultMetadata(status=ResultStatus.EMPTY, **metadata_values),
                    message="未找到匹配的物料或仓库",
                )
            graph = self._build_graph(material_id, warehouse_id, as_of_date)
        except Exception:
            return EvidenceGraphResult(
                metadata=ResultMetadata(status=ResultStatus.ERROR, **metadata_values),
                message="证据图依赖查询失败",
                errors=["repository_error"],
            )

        dangling = self._dangling_nodes(graph)
        if dangling:
            return EvidenceGraphResult(
                metadata=ResultMetadata(status=ResultStatus.BLOCKED, **metadata_values),
                message="证据图存在悬空业务节点",
                nodes=self._nodes(graph),
                edges=self._edges(graph),
                blockers=["dangling_graph_node"],
            )

        started = monotonic()
        start = self._node_id(EvidenceNodeType.MATERIAL, material_id)
        related = [node for node in graph.nodes if node != start]
        paths: list[EvidencePath] = []
        selected: set[str] = {start}
        selected_edges: set[tuple[str, str]] = set()
        for target in sorted(related):
            if (monotonic() - started) * 1000 > timeout_ms:
                return self._limited(metadata_values, "graph_query_timeout")
            try:
                path = nx.shortest_path(graph, start, target)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            hops = len(path) - 1
            if hops > max_hops:
                continue
            new_nodes = selected | set(path)
            if len(new_nodes) > max_nodes:
                return self._limited(metadata_values, "graph_node_limit_exceeded")
            pairs = list(nx.utils.pairwise(path))
            relations = [graph.edges[left, right]["relation_type"] for left, right in pairs]
            paths.append(EvidencePath(node_ids=path, relation_types=relations))
            selected = new_nodes
            selected_edges.update(tuple(sorted((left, right))) for left, right in pairs)

        if not paths:
            return EvidenceGraphResult(
                metadata=ResultMetadata(status=ResultStatus.EMPTY, **metadata_values),
                message="目标物料没有可用关系路径",
            )
        subgraph = graph.edge_subgraph(selected_edges).copy()
        subgraph.add_nodes_from((node, graph.nodes[node]) for node in selected)
        return EvidenceGraphResult(
            metadata=ResultMetadata(status=ResultStatus.OK, **metadata_values),
            message="证据关系路径查询完成",
            nodes=self._nodes(subgraph),
            edges=self._edges(subgraph),
            paths=paths,
        )

    def _build_graph(self, material_id: str, warehouse_id: str, as_of_date: date) -> nx.Graph:
        graph = nx.Graph()
        material = self._repository.get_material(material_id)
        warehouse = self._repository.get_warehouse(warehouse_id)
        assert material is not None and warehouse is not None
        material_node = self._add_node(graph, EvidenceNodeType.MATERIAL, material.material_id)
        warehouse_node = self._add_node(graph, EvidenceNodeType.WAREHOUSE, warehouse.warehouse_id)
        movements = self._repository.list_movements(
            material_id=material_id, warehouse_id=warehouse_id, as_of_date=as_of_date
        )
        if movements:
            graph.add_edge(
                material_node,
                warehouse_node,
                relation_type=EvidenceRelationType.STORED_IN,
                source_id="|".join(sorted(item.movement_id for item in movements)),
            )
        for order in self._repository.list_purchase_orders(material_id, warehouse_id):
            if order.actual_date is not None and order.actual_date <= as_of_date:
                order_node = self._add_node(graph, EvidenceNodeType.PURCHASE_ORDER, order.po_id)
                graph.add_edge(
                    order_node,
                    material_node,
                    relation_type=EvidenceRelationType.PURCHASES,
                    source_id=order.po_id,
                )
        for order in self._repository.list_production_orders(material_id, warehouse_id):
            if order.planned_start <= as_of_date:
                order_node = self._add_node(
                    graph, EvidenceNodeType.PRODUCTION_ORDER, order.production_order_id
                )
                graph.add_edge(
                    order_node,
                    material_node,
                    relation_type=EvidenceRelationType.CONSUMES,
                    source_id=order.production_order_id,
                )
        return graph

    @staticmethod
    def _node_id(node_type: EvidenceNodeType, source_id: str) -> str:
        return f"{node_type.value}:{source_id}"

    def _add_node(self, graph: nx.Graph, node_type: EvidenceNodeType, source_id: str) -> str:
        node_id = self._node_id(node_type, source_id)
        graph.add_node(
            node_id,
            node_type=node_type,
            source_type=node_type.value.lower(),
            source_id=source_id,
            synthetic=True,
        )
        return node_id

    @staticmethod
    def _dangling_nodes(graph: nx.Graph) -> list[str]:
        business_types = {EvidenceNodeType.PURCHASE_ORDER, EvidenceNodeType.PRODUCTION_ORDER}
        return sorted(
            node
            for node, data in graph.nodes(data=True)
            if data["node_type"] in business_types and graph.degree(node) == 0
        )

    @staticmethod
    def _nodes(graph: nx.Graph) -> list[EvidenceGraphNode]:
        return [
            EvidenceGraphNode(node_id=node, **data)
            for node, data in sorted(graph.nodes(data=True))
        ]

    @staticmethod
    def _edges(graph: nx.Graph) -> list[EvidenceGraphEdge]:
        return [
            EvidenceGraphEdge(
                source_node_id=left,
                target_node_id=right,
                relation_type=data["relation_type"],
                source_id=data["source_id"],
            )
            for left, right, data in sorted(graph.edges(data=True))
        ]

    @staticmethod
    def _limited(metadata_values: dict, blocker: str) -> EvidenceGraphResult:
        return EvidenceGraphResult(
            metadata=ResultMetadata(status=ResultStatus.BLOCKED, **metadata_values),
            message="证据图查询触发安全限制",
            blockers=[blocker],
        )
