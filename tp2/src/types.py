from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

MAX_NEIGHBORS = 4

"""
                            (Neighbor)
Content -> Server -> Node -> Node... -> Client
          \\______________ Flow _____________/  
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
    """Low-level record of one content stream passing through a node.

    Don't build these directly: go through NetworkNode.add_downstream(), which
    enforces the neighbor rules. A flow always has at least one downstream.
    """
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
        """No one downstream wants this flow: it should be destroyed."""
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
    # Keyed by content id: a node holds at most ONE flow per content. More
    # viewers of the same content just means more downstreams on that flow.
    flows: dict[int, Flow] = field(default_factory=dict)
    _next_flow_id: int = field(default=1, init=False, repr=False)

    # -- neighbors --------------------------------------------------------- #
    @property
    def is_full(self) -> bool:
        return len(self.neighbors) >= MAX_NEIGHBORS

    def add_neighbor(self, id: int, ip: str, rtt_ms: float | None = None) -> bool:
        """Returns False if the node is already full (and the neighbor is new)."""
        if ip in self.neighbors:
            self.neighbors[ip].update_metrics(rtt_ms=rtt_ms)
            return True
        if self.is_full:
            return False
        self.neighbors[ip] = Neighbor(id, ip, rtt_ms)
        return True

    def remove_neighbor(self, ip: str) -> list[Flow]:
        """Drop a neighbor and clean up every flow that touched it.

        - Flows that were casting to it just lose that downstream (and are
          destroyed if that leaves them with no downstreams).
        - Flows that were coming from it have no valid upstream any more, so
          they are destroyed too. Those flows are returned, so the caller can
          warn their downstreams or look for another route.
        """
        self.neighbors.pop(ip, None)
        lost_upstream: list[Flow] = []
        for flow in list(self.flows.values()):
            if flow.upstream == ip:
                lost_upstream.append(flow)
                self._destroy_flow(flow.content_id)
            else:
                flow.remove_downstream(ip)
                if flow.is_unused:
                    self._destroy_flow(flow.content_id)
        return lost_upstream

    def closest_neighbors(self, n: int = 3) -> list[Neighbor]:
        known = [nb for nb in self.neighbors.values() if nb.rtt_ms is not None]
        return sorted(known, key=lambda nb: nb.rtt_ms)[:n]

    # -- sources ----------------------------------------------------------- #
    def can_originate(self, content_id: int) -> bool:
        """Whether this node is the source of a content (only servers are)."""
        return False

    # -- flows ------------------------------------------------------------- #
    def flow_for_content(self, content_id: int) -> Flow | None:
        return self.flows.get(content_id)

    def add_downstream(
        self,
        content_id: int,
        ip: str,
        upstream: str | None = None,
        flow_id: int | None = None,
    ) -> Flow:
        """Cast a content to a neighbor, creating the flow if needed.

        - `ip` must be a neighbor.
        - A flow has exactly one upstream, which must be a neighbor. `upstream`
          is only needed when the flow is created; it may be omitted afterwards
          but can't contradict the existing one.
        - A node that is the source of the content (a server) has no upstream.
        - A flow never casts back to its own upstream.
        """
        if ip not in self.neighbors:
            raise ValueError(f"{ip} is not a neighbor of {self.ip}")

        flow = self.flows.get(content_id)
        if flow is None:
            if self.can_originate(content_id):
                if upstream is not None:
                    raise ValueError(f"{self.ip} is the source of content {content_id}")
            elif upstream is None:
                raise ValueError(f"flow for content {content_id} needs an upstream")
            elif upstream not in self.neighbors:
                raise ValueError(f"{upstream} is not a neighbor of {self.ip}")

            if flow_id is None:
                flow_id = self._next_flow_id
            self._next_flow_id = max(self._next_flow_id, flow_id) + 1
            flow = Flow(id=flow_id, content_id=content_id, upstream=upstream)
            self.flows[content_id] = flow
        elif upstream is not None and upstream != flow.upstream:
            raise ValueError(
                f"content {content_id} already comes from {flow.upstream}, not {upstream}"
            )

        if ip == flow.upstream:
            raise ValueError("a flow can't be cast back to its upstream")
        flow.add_downstream(ip)
        return flow

    def remove_downstream(self, content_id: int, ip: str) -> bool:
        """Stop casting a content to `ip`. Returns True if the flow was destroyed.

        A flow with no downstreams is destroyed automatically.
        TODO: instead, send FLOWKILL to the upstream (recursively towards the
        source) and only destroy the flow once that is confirmed.
        """
        flow = self.flows.get(content_id)
        if flow is None:
            return False
        flow.remove_downstream(ip)
        if flow.is_unused:
            self._destroy_flow(content_id)
            return True
        return False

    def _destroy_flow(self, content_id: int) -> None:
        self.flows.pop(content_id, None)

    def flows_via(self, neighbor_ip: str) -> list[Flow]:
        """Flows that depend on a neighbor (useful when it disconnects)."""
        return [
            f for f in self.flows.values()
            if f.upstream == neighbor_ip or neighbor_ip in f.downstreams
        ]

    # -- telling downstreams who feeds us ---------------------------------- #
    def upstream_of(self, content_id: int) -> str | None:
        flow = self.flows.get(content_id)
        return flow.upstream if flow else None

    def upstreams_for(self, downstream_ip: str) -> dict[int, str]:
        """{content_id: upstream ip} for every flow this node casts to
        `downstream_ip`, i.e. what to tell that node about who feeds us.
        Flows this node originates (no upstream) are left out."""
        return {
            f.content_id: f.upstream
            for f in self.flows.values()
            if downstream_ip in f.downstreams and f.upstream is not None
        }

    @property
    def streams(self) -> list[int]:
        """Content ids this node is currently receiving/relaying."""
        return sorted(c for c, f in self.flows.items() if f.active)


# --------------------------------------------------------------------------- #
# Server (Subclass of NetworkNode)
# --------------------------------------------------------------------------- #
@dataclass
class Server(NetworkNode):
    """A node that is also the source of some contents. It has the same
    neighbor limit and flow rules as any node (its flows just have no upstream)."""
    contents: set[int] = field(default_factory=set)       # content ids it can serve

    def serves(self, content_id: int) -> bool:
        return content_id in self.contents

    def can_originate(self, content_id: int) -> bool:
        return self.serves(content_id)

    def add_content(self, content_id: int) -> None:
        self.contents.add(content_id)

    def remove_content(self, content_id: int) -> None:
        self.contents.discard(content_id)


if __name__ == "__main__":
    # S -> A -> B, and A -> C (same content, so same flow)
    server = Server(id=0, ip="10.0.0.1", contents={7})
    server.add_neighbor(1, "10.0.0.2")
    server.add_downstream(7, "10.0.0.2")

    a = NetworkNode(id=1, ip="10.0.0.2")
    for i, ip in enumerate(["10.0.0.1", "10.0.0.3", "10.0.0.4"]):
        a.add_neighbor(i + 10, ip)
    a.add_downstream(7, "10.0.0.3", upstream="10.0.0.1")
    a.add_downstream(7, "10.0.0.4")           # same flow, one more downstream
    assert len(a.flows) == 1
    print(a.upstreams_for("10.0.0.3"))        # {7: '10.0.0.1'}

    for bad in (
        lambda: a.add_downstream(7, "10.9.9.9"),                  # not a neighbor
        lambda: a.add_downstream(7, "10.0.0.3", upstream="10.0.0.4"),  # second upstream
        lambda: a.add_downstream(7, "10.0.0.1"),                  # back to upstream
        lambda: a.add_downstream(8, "10.0.0.3"),                  # no upstream given
    ):
        try:
            bad()
        except ValueError as e:
            print("rejected:", e)

    a.remove_downstream(7, "10.0.0.3")
    print(a.remove_downstream(7, "10.0.0.4"))  # True: no downstreams left, flow destroyed
    print(a.flows)                             # {}