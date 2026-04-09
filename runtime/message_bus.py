import copy


class MessageBus:

    def __init__(self, event_bus=None):
        self.messages = []
        self._event_bus = event_bus

    def clear(self):
        self.messages = []

    def publish(self, message, metadata=None):
        msg = copy.deepcopy(message)

        if metadata:
            msg["metadata"] = metadata
        self.messages.append(msg)

        if self._event_bus:
            from core.events import EventType
            self._event_bus.publish(EventType.MESSAGE_PUBLISHED, {
                "agent": msg.get("agent"),
                "role": msg.get("role"),
            })

    def history(self):
        return self.messages

    def last(self):
        if self.messages:
            return self.messages[-1]
        return None
