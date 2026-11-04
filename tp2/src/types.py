from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

MAX_NEIGHBORS = 4

"""
                            (Neighbor)
Content -> Server -> Node -> Node... -> Client
          \______________ Flow _____________/  
"""

# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
@dataclass
class Content:
    id: int
    file: Path | str            # path to the file, or a stream source
    name: str = ""              # human-readable label (optional)

    def __post_init__(self) -> None:
        self.file = Path(self.file)


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
@dataclass
class Server:
    id: int
    ip: str
    contents: set[int] = field(default_factory=set)       # content ids it can serve
    attached_nodes: list[str] = field(default_factory=list)  # overlay nodes it feeds (ips)

    def serves(self, content_id: int) -> bool:
        return content_id in self.contents

    def add_content(self, content_id: int) -> None:
        self.contents.add(content_id)

    def remove_content(self, content_id: int) -> None:
        self.contents.discard(content_id)

    def attach(self, node_ip: str) -> bool:
        if self.attached_nodes >= MAX_NEIGHBORS:
            return False
        else:
            self.attached_nodes.append(node_ip)
            return True


# --------------------------------------------------------------------------- #
# Neighbor (replaces the bare "ip[]" so you can store link metrics)
# --------------------------------------------------------------------------- #
@dataclass
class Neighbor:
    id: int
    ip: str
    rtt_ms: float | None = None      # from health checks
    loss: float = 0.0                # 0.0 - 1.0, measured on UDP streams
    load: float = 0.0
    last_seen: float = field(default_factory=time.monotonic)

    #health check - should store metrics as well, should be performed every 0.5-1s
    def touch(self) -> None: 
        self.last_seen = time.monotonic()

    #metric updates - should be updated with averages (3?)
    def update_metrics(self, rtt_ms: float | None = None, loss: float | None = None) -> None:
        if rtt_ms is not None:
            self.rtt_ms = rtt_ms
        if loss is not None:
            self.loss = loss
        self.touch()


# --------------------------------------------------------------------------- #
# Flow
# --------------------------------------------------------------------------- #
class FlowState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"


@dataclass
class Flow:
    id: int
    content_id: int
    upstream: str | None = None                  # previous node's ip (None at the source)
    downstreams: set[str] = field(default_factory=set)  # ips to forward to
    hops: int = 0                                # distance from the server
    state: FlowState = FlowState.INACTIVE

    # -- state ------------------------------------------------------------- #
    @property
    def active(self) -> bool:
        return self.state is FlowState.ACTIVE

    def activate(self) -> None:
        self.state = FlowState.ACTIVE

    def deactivate(self) -> None:
        self.state = FlowState.INACTIVE

    # -- destinations ------------------------------------------------------ #
    def add_downstream(self, ip: str) -> None:
        self.downstreams.add(ip)

    def remove_downstream(self, ip: str) -> None:
        self.downstreams.discard(ip)

    @property
    def is_unused(self) -> bool:
        """No one downstream wants this flow: the node can ask upstream to stop."""
        return not self.downstreams

    def forward_targets(self) -> set[str]:
        """Where packets should be sent right now (empty if the flow is off)."""
        return set(self.downstreams) if self.active else set()


# --------------------------------------------------------------------------- #
# Network node
# --------------------------------------------------------------------------- #
@dataclass
class NetworkNode:
    id: int
    ip: str
    neighbors: dict[str, Neighbor] = field(default_factory=dict)  # keyed by ip
    flows: dict[int, Flow] = field(default_factory=dict)          # keyed by flow id

    # -- neighbors --------------------------------------------------------- #
    @property
    def is_full(self) -> bool:
        return len(self.neighbors) >= MAX_NEIGHBORS

    def add_neighbor(self, ip: str, rtt_ms: float | None = None) -> bool:
        """Returns False if the node is already full (and the neighbor is new)."""
        if ip in self.neighbors:
            self.neighbors[ip].update_metrics(rtt_ms=rtt_ms)
            return True
        if self.is_full:
            return False
        self.neighbors[ip] = Neighbor(ip, rtt_ms)
        return True

    def remove_neighbor(self, ip: str) -> None:
        self.neighbors.pop(ip, None)

    def closest_neighbors(self, n: int = 3) -> list[Neighbor]:
        known = [nb for nb in self.neighbors.values() if nb.rtt_ms is not None]
        return sorted(known, key=lambda nb: nb.rtt_ms)[:n]

    # -- flows ------------------------------------------------------------- #
    def add_flow(self, flow: Flow) -> None:
        self.flows[flow.id] = flow

    def remove_flow(self, flow_id: int) -> None:
        self.flows.pop(flow_id, None)

    def flows_for_content(self, content_id: int) -> list[Flow]:
        return [f for f in self.flows.values() if f.content_id == content_id]

    def flows_via(self, neighbor_ip: str) -> list[Flow]:
        """Flows that depend on a neighbor (useful when it disconnects)."""
        return [
            f for f in self.flows.values()
            if f.upstream == neighbor_ip or neighbor_ip in f.downstreams
        ]

    @property
    def streams(self) -> list[int]:
        """Content ids this node is currently receiving/relaying."""
        return sorted({f.content_id for f in self.flows.values() if f.active})


if __name__ == "__main__":
    node = NetworkNode(id=1, ip="10.0.0.2")
    node.add_neighbor("10.0.1.2", rtt_ms=12.5)
    node.add_neighbor("10.0.2.2", rtt_ms=4.0)

    flow = Flow(id=1, content_id=7, upstream="10.0.2.2", hops=2)
    flow.add_downstream("10.0.1.2")
    flow.activate()
    node.add_flow(flow)

    print(node.streams)                                   # [7]
    print([nb.ip for nb in node.closest_neighbors(1)])    # ['10.0.2.2']