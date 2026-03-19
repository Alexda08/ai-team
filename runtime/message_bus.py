import copy

class MessageBus:

    def __init__(self):
        self.messages = []

    def publish(self, message, metadata=None):
        msg = copy.deepcopy(message)

        if metadata:
            msg["metadata"] = metadata
        self.messages.append(msg)

    def history(self):
        return self.messages

    def last(self):
        if self.messages:
            return self.messages[-1]
        return None