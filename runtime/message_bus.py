class MessageBus:

    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)

    def history(self):
        return self.messages

    def last(self):
        if self.messages:
            return self.messages[-1]
        return None