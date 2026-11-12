"""Per-viewer presence selection. No persistence or gameplay decisions live here."""
import math

ENTER_RANGE = 1200
EXIT_RANGE = 1400
IDENTITY = ('nickname', 'avatar', 'color')


class SpatialIndex:
    def __init__(self, states):
        self.states = {state['id']: state for state in states}
        self.cells = {}
        for state in self.states.values():
            if not state.get('inClassroom'):
                key = self.cell(state)
                self.cells.setdefault(key, []).append(state)

    @staticmethod
    def cell(state):
        return (state.get('floor', 'campus'), math.floor(state['x'] / EXIT_RANGE),
                math.floor(state['y'] / EXIT_RANGE))

    def candidates(self, observer, overview=False):
        floor, cx, cy = self.cell(observer)
        if overview:
            return (state for state in self.states.values()
                    if state.get('floor', 'campus') == floor and not state.get('inClassroom'))
        return (state for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for state in self.cells.get((floor, cx + dx, cy + dy), ()))


class InterestView:
    def __init__(self):
        self.visible = set()
        self.identities = {}
        self.observer_key = None

    def packet(self, index, observer, changed, compact=True, overview=False, reconcile=False):
        # A stationary viewer only needs distances checked for changed actors.
        # Moving viewers and roster changes still discover stationary neighbours.
        key = (observer['id'], observer.get('floor', 'campus'), observer['x'], observer['y'],
               bool(observer.get('inClassroom')), overview)
        refresh = reconcile or key != self.observer_key
        visible = set() if refresh else (self.visible - changed) & index.states.keys()
        if not observer.get('inClassroom'):
            candidates = (index.candidates(observer, overview) if refresh else
                          (state for pid in changed if (state := index.states.get(pid)) is not None
                           and state.get('floor', 'campus') == observer.get('floor', 'campus')
                           and not state.get('inClassroom')))
            for state in candidates:
                pid = state['id']
                if pid == observer['id']:
                    continue
                reach = EXIT_RANGE if pid in self.visible else ENTER_RANGE
                if overview or math.hypot(state['x'] - observer['x'], state['y'] - observer['y']) <= reach:
                    visible.add(pid)
        entered = visible - self.visible
        hidden = self.visible - visible
        rows = []
        for pid in sorted(entered | (visible & changed)):
            state = index.states[pid]
            identity = tuple(state.get(key) for key in IDENTITY)
            full = not compact or pid in entered or self.identities.get(pid) != identity
            rows.append({**{key: value for key, value in state.items()
                            if full or key not in IDENTITY}, 'inView': True})
            self.identities[pid] = identity
        for pid in hidden:
            self.identities.pop(pid, None)
        self.visible = visible
        self.observer_key = key
        packet = {'type': 'moves', 'players': rows}
        if hidden:
            packet['hidden'] = sorted(hidden)
        return packet
