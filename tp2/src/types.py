from __future__ import annotations

import time
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from types import MappingProxyType

MAX_NEIGHBORS = 4

"""
                            (Neighbor)
Content -> Server -> Node -> Node... -> Client
          \______________ Flow _____________/  
"""

# All state is private (_name) and exposed through read-only properties.
# Anything that changes state goes through a method. Flows in particular are
# only ever changed through NetworkNode methods (which enforce the neighbor
# rules); the underscore-prefixed Flow methods are for use inside this module.


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
class Content:
    def __init__(self, id: int, file: Path | str, name: str = "") -> None:
        self._id = id
        self._file = Path(file)      # path to the file, or a stream source
        self._name = name            # human-readable label (optional)

    @property
    def id(self) -> int:
        return self._id

    @property
    def file(self) -> Path:
        return self._file

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"Content(id={self._id}, file={str(self._file)!r}, name={self._name!r})"


# --------------------------------------------------------------------------- #
# Neighbor (replaces the bare "ip[]" so you can store link metrics)
# --------------------------------------------------------------------------- #
class Neighbor:
    def __init__(self, ip: str, rtt_ms: float | None = None) -> None:
        self._ip = ip
        self._rtt_ms = rtt_ms                    # from health checks
        self._loss = 0.0                         # 0.0 - 1.0, measured on UDP streams
        self._load = 0.0
        self._last_seen = time.monotonic()

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def rtt_ms(self) -> float | None:
        return self._rtt_ms

    @property
    def loss(self) -> float:
        return self._loss

    @property
    def load(self) -> float:
        return self._load

    @property
    def last_seen(self) -> float:
        return self._last_seen

    #health check - should store metrics as well, should be performed every 0.5-1s
    def touch(self) -> None:
        self._last_seen = time.monotonic()

    #metric updates - should be updated with averages (3?)
    def update_metrics(
        self,
        rtt_ms: float | None = None,
        loss: float | None = None,
        load: float | None = None,
    ) -> None:
        if rtt_ms is not None:
            self._rtt_ms = rtt_ms
        if loss is not None:
            self._loss = loss
        if load is not None:
            self._load = load
        self.touch()

    def __repr__(self) -> str:
        return f"Neighbor(ip={self._ip!r}, rtt_ms={self._rtt_ms}, loss={self._loss}, load={self._load})"


# --------------------------------------------------------------------------- #
# Flow
# --------------------------------------------------------------------------- #
class FlowState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    ORPHANED = "orphaned"    # lost its upstream; waiting to be recovered


class Flow:
    """One content stream passing through a node.

    Read it through the properties; change it through NetworkNode methods.
    A flow always has at least one downstream (it is destroyed otherwise).
    """

    def __init__(self, id: int, content_id: int, upstream: str | None, hops: int = 0) -> None:
        self._id = id
        self._content_id = content_id
        self._upstream = upstream                # previous node's ip (None at the source, or when orphaned)
        self._downstreams: set[str] = set()      # ips to forward to
        self._hops = hops                        # distance from the server
        self._state = FlowState.INACTIVE

    # -- read access ------------------------------------------------------- #
    @property
    def id(self) -> int:
        return self._id

    @property
    def content_id(self) -> int:
        return self._content_id

    @property
    def upstream(self) -> str | None:
        return self._upstream

    @property
    def downstreams(self) -> frozenset[str]:
        return frozenset(self._downstreams)

    @property
    def hops(self) -> int:
        return self._hops

    @property
    def state(self) -> FlowState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state is FlowState.ACTIVE

    @property
    def orphaned(self) -> bool:
        return self._state is FlowState.ORPHANED

    @property
    def is_unused(self) -> bool:
        """No one downstream wants this flow: it should be destroyed."""
        return not self._downstreams

    def forward_targets(self) -> frozenset[str]:
        """Where packets should be sent right now (empty unless the flow is active)."""
        return self.downstreams if self.active else frozenset()

    # -- internal mutators (called by NetworkNode) ------------------------- #
    def _add_downstream(self, ip: str) -> None:
        self._downstreams.add(ip)

    def _remove_downstream(self, ip: str) -> None:
        self._downstreams.discard(ip)

    def _activate(self) -> None:
        if self.orphaned:
            raise ValueError(f"flow {self._id} is orphaned; recover it first")
        self._state = FlowState.ACTIVE

    def _deactivate(self) -> None:
        if not self.orphaned:
            self._state = FlowState.INACTIVE

    def _orphan(self) -> None:
        self._upstream = None
        self._state = FlowState.ORPHANED

    def _set_upstream(self, ip: str, hops: int | None = None) -> None:
        self._upstream = ip
        if hops is not None:
            self._hops = hops
        self._state = FlowState.INACTIVE         # active again once the caller confirms it

    def __repr__(self) -> str:
        return (
            f"Flow(id={self._id}, content={self._content_id}, upstream={self._upstream!r}, "
            f"downstreams={sorted(self._downstreams)}, state={self._state.value})"
        )


# --------------------------------------------------------------------------- #
# Network node
# --------------------------------------------------------------------------- #
class NetworkNode:
    def __init__(self, ip: str) -> None:
        self._ip = ip
        self._neighbors: dict[str, Neighbor] = {}     # keyed by ip
        # Keyed by content id: a node holds at most ONE flow per content. More
        # viewers of the same content just means more downstreams on that flow.
        self._flows: dict[int, Flow] = {}
        self._next_flow_id = 1

    # -- read access ------------------------------------------------------- #
    @property
    def ip(self) -> str:
        return self._ip

    @property
    def neighbors(self) -> Mapping[str, Neighbor]:
        """Read-only live view, keyed by ip."""
        return MappingProxyType(self._neighbors)

    @property
    def flows(self) -> Mapping[int, Flow]:
        """Read-only live view, keyed by content id."""
        return MappingProxyType(self._flows)

    # -- neighbors --------------------------------------------------------- #
    @property
    def is_full(self) -> bool:
        return len(self._neighbors) >= MAX_NEIGHBORS

    def add_neighbor(self, ip: str, rtt_ms: float | None = None) -> bool:
        """Returns False if the node is already full (and the neighbor is new)."""
        if ip == self._ip:
            raise ValueError("a node can't be its own neighbor")
        if ip in self._neighbors:
            self._neighbors[ip].update_metrics(rtt_ms=rtt_ms)
            return True
        if self.is_full:
            return False
        self._neighbors[ip] = Neighbor(ip, rtt_ms)
        return True

    def remove_neighbor(self, ip: str) -> list[Flow]:
        """Drop a neighbor and fix up every flow that touched it.

        - Flows that were casting to it lose that downstream (and are
          destroyed if that leaves them with no downstreams).
        - Flows that were coming from it become ORPHANED: they keep their
          downstreams but stop forwarding until recover_flow() gives them a
          new upstream. The orphaned flows are returned, so the caller can
          warn their downstreams or look for another route.
        """
        self._neighbors.pop(ip, None)
        orphaned: list[Flow] = []
        for flow in list(self._flows.values()):
            if flow.upstream == ip:
                flow._orphan()
                orphaned.append(flow)
            else:
                flow._remove_downstream(ip)
                if flow.is_unused:
                    self._destroy_flow(flow.content_id)
        return orphaned

    def closest_neighbors(self, n: int = 3) -> list[Neighbor]:
        known = [nb for nb in self._neighbors.values() if nb.rtt_ms is not None]
        return sorted(known, key=lambda nb: nb.rtt_ms)[:n]

    # -- sources ----------------------------------------------------------- #
    def can_originate(self, content_id: int) -> bool:
        """Whether this node is the source of a content (only servers are)."""
        return False

    # -- flows ------------------------------------------------------------- #
    def flow_for_content(self, content_id: int) -> Flow | None:
        return self._flows.get(content_id)

    @property
    def orphaned_flows(self) -> list[Flow]:
        return [f for f in self._flows.values() if f.orphaned]

    def add_downstream(
        self,
        content_id: int,
        ip: str,
        upstream: str | None = None,
        flow_id: int | None = None,
        hops: int = 0,
    ) -> Flow:
        """Cast a content to a neighbor, creating the flow if needed.

        - `ip` must be a neighbor.
        - A flow has exactly one upstream, which must be a neighbor. `upstream`
          (and `flow_id`, `hops`) only matter when the flow is created; later
          calls may omit `upstream` but can't contradict the existing one.
        - A node that is the source of the content (a server) has no upstream.
        - A flow never casts back to its own upstream.
        """
        if ip not in self._neighbors:
            raise ValueError(f"{ip} is not a neighbor of {self._ip}")

        flow = self._flows.get(content_id)
        if flow is None:
            if self.can_originate(content_id):
                if upstream is not None:
                    raise ValueError(f"{self._ip} is the source of content {content_id}")
            elif upstream is None:
                raise ValueError(f"flow for content {content_id} needs an upstream")
            elif upstream not in self._neighbors:
                raise ValueError(f"{upstream} is not a neighbor of {self._ip}")

            if flow_id is None:
                flow_id = self._next_flow_id
            self._next_flow_id = max(self._next_flow_id, flow_id) + 1
            flow = Flow(flow_id, content_id, upstream, hops)
            self._flows[content_id] = flow
        elif upstream is not None and upstream != flow.upstream:
            if flow.orphaned:
                raise ValueError(f"content {content_id} has no upstream; use recover_flow()")
            raise ValueError(
                f"content {content_id} already comes from {flow.upstream}, not {upstream}"
            )

        if ip == flow.upstream:
            raise ValueError("a flow can't be cast back to its upstream")
        flow._add_downstream(ip)
        return flow

    def remove_downstream(self, content_id: int, ip: str) -> bool:
        """Stop casting a content to `ip`. Returns True if the flow was destroyed.

        A flow with no downstreams is destroyed automatically.
        TODO: instead, send FLOWKILL to the upstream (recursively towards the
        source) and only destroy the flow once that is confirmed.
        """
        flow = self._flows.get(content_id)
        if flow is None:
            return False
        flow._remove_downstream(ip)
        if flow.is_unused:
            self._destroy_flow(content_id)
            return True
        return False

    def recover_flow(self, content_id: int, upstream: str, hops: int | None = None) -> Flow:
        """Give an orphaned flow a new upstream (which must be a neighbor).

        The flow comes back INACTIVE; call activate_flow() once the new
        upstream has confirmed it.
        """
        flow = self._require_flow(content_id)
        if not flow.orphaned:
            raise ValueError(f"flow for content {content_id} isn't orphaned")
        if upstream not in self._neighbors:
            raise ValueError(f"{upstream} is not a neighbor of {self._ip}")
        if upstream in flow.downstreams:
            raise ValueError("a flow can't be fed by one of its own downstreams")
        flow._set_upstream(upstream, hops)
        return flow

    def activate_flow(self, content_id: int) -> None:
        self._require_flow(content_id)._activate()

    def deactivate_flow(self, content_id: int) -> None:
        self._require_flow(content_id)._deactivate()

    def _require_flow(self, content_id: int) -> Flow:
        flow = self._flows.get(content_id)
        if flow is None:
            raise ValueError(f"no flow for content {content_id} on {self._ip}")
        return flow

    def _destroy_flow(self, content_id: int) -> None:
        self._flows.pop(content_id, None)

    def flows_via(self, neighbor_ip: str) -> list[Flow]:
        """Flows that depend on a neighbor (useful when it disconnects)."""
        return [
            f for f in self._flows.values()
            if f.upstream == neighbor_ip or neighbor_ip in f.downstreams
        ]

    # -- telling downstreams who feeds us ---------------------------------- #
    def upstream_of(self, content_id: int) -> str | None:
        flow = self._flows.get(content_id)
        return flow.upstream if flow else None

    def upstreams_for(self, downstream_ip: str) -> dict[int, str]:
        """{content_id: upstream ip} for every flow this node casts to
        `downstream_ip`, i.e. what to tell that node about who feeds us.
        Flows this node originates or that are orphaned (no upstream) are left out."""
        return {
            f.content_id: f.upstream
            for f in self._flows.values()
            if downstream_ip in f.downstreams and f.upstream is not None
        }

    @property
    def streams(self) -> list[int]:
        """Content ids this node is currently receiving/relaying."""
        return sorted(c for c, f in self._flows.items() if f.active)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(ip={self._ip!r}, neighbors={list(self._neighbors)})"


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class Server(NetworkNode):
    """A node that is also the source of some contents. It has the same
    neighbor limit and flow rules as any node (its flows just have no upstream)."""

    def __init__(self, ip: str, contents: set[int] | frozenset[int] = frozenset()) -> None:
        super().__init__(ip)
        self._contents = set(contents)           # content ids it can serve

    @property
    def contents(self) -> frozenset[int]:
        return frozenset(self._contents)

    def serves(self, content_id: int) -> bool:
        return content_id in self._contents

    def can_originate(self, content_id: int) -> bool:
        return self.serves(content_id)

    def add_content(self, content_id: int) -> None:
        self._contents.add(content_id)

    def remove_content(self, content_id: int) -> None:
        self._contents.discard(content_id)


if __name__ == "__main__":
    # S -> A -> B, and A -> C (same content, so same flow)
    server = Server("10.0.0.1", contents={7})
    server.add_neighbor("10.0.0.2")
    server.add_downstream(7, "10.0.0.2")

    a = NetworkNode("10.0.0.2")
    for ip in ("10.0.0.1", "10.0.0.3", "10.0.0.4"):
        a.add_neighbor(ip)
    a.add_downstream(7, "10.0.0.3", upstream="10.0.0.1")
    a.add_downstream(7, "10.0.0.4")           # same flow, one more downstream
    a.activate_flow(7)
    assert len(a.flows) == 1
    print(a.upstreams_for("10.0.0.3"))        # {7: '10.0.0.1'}

    # strict handling: state can't be changed from outside
    for bad in (
        lambda: a.add_downstream(7, "10.9.9.9"),                       # not a neighbor
        lambda: a.add_downstream(7, "10.0.0.3", upstream="10.0.0.4"),  # second upstream
        lambda: a.add_downstream(7, "10.0.0.1"),                       # back to upstream
        lambda: a.add_downstream(8, "10.0.0.3"),                       # no upstream given
        lambda: setattr(a.flow_for_content(7), "upstream", "x"),       # read-only property
        lambda: a.flow_for_content(7).downstreams.add("x"),            # frozenset
    ):
        try:
            bad()
        except (ValueError, AttributeError) as e:
            print("rejected:", e)

    # upstream dies -> flow is orphaned, not destroyed
    orphaned = a.remove_neighbor("10.0.0.1")
    print(orphaned, a.flow_for_content(7).forward_targets())   # [...orphaned] frozenset()
    try:
        a.activate_flow(7)
    except ValueError as e:
        print("rejected:", e)

    # ...and later recovered through another neighbor
    a.add_neighbor("10.0.0.5")
    a.recover_flow(7, upstream="10.0.0.5", hops=3)
    a.activate_flow(7)
    print(a.flow_for_content(7), a.streams)

    a.remove_downstream(7, "10.0.0.3")
    print(a.remove_downstream(7, "10.0.0.4"))  # True: no downstreams left, flow destroyed
    print(dict(a.flows))                       # {}