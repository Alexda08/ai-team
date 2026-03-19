class ScoreSystem:
    def __init__(self):
        self.score = 0


    def add(self, points, reason=""):
        self.score += points
        print(f"[SCORE] {points:+} → {reason} | Total: {self.score}")