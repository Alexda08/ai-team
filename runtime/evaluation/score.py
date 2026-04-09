class ScoreSystem:
    def __init__(self, event_bus=None):
        self.score = 0
        self._event_bus = event_bus

    def add(self, points, reason=""):
        self.score += points
        print(f"[SCORE] {points:+} -> {reason} | Total: {self.score}")
        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.EVAL_END, {
                "points": points,
                "total": self.score,
                "reason": reason,
            })
