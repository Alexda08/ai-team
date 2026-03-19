class ScoreBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def history(self):
        return self.events
        
    def export(self):
        return self.events