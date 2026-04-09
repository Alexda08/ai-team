from typing import TypeVar, Generic, Dict, Callable, Optional, Tuple, Any

T = TypeVar("T")


class RegistryBase(Generic[T]):
    """Generic registry with class-specific isolation.

    Each subclass gets its own isolated entry dict via a dynamically-named
    class attribute, so AgentRegistry and ToolRegistry never collide.
    """

    @classmethod
    def _entries(cls) -> Dict[str, T]:
        attr = f"_registry_entries_{cls.__name__}"
        if not hasattr(cls, attr):
            setattr(cls, attr, {})
        return getattr(cls, attr)

    @classmethod
    def register(cls, name: str) -> Callable:
        """Decorator to register a class/value under *name*."""
        def decorator(obj: T) -> T:
            cls._entries()[name] = obj
            return obj
        return decorator

    @classmethod
    def register_value(cls, name: str, value: T) -> T:
        """Imperative registration (non-decorator)."""
        cls._entries()[name] = value
        return value

    @classmethod
    def get(cls, name: str) -> T:
        """Return the registered item or raise KeyError."""
        return cls._entries()[name]

    @classmethod
    def create(cls, name: str, *args: Any, **kwargs: Any) -> Any:
        """Instantiate the registered class with given arguments."""
        factory = cls.get(name)
        return factory(*args, **kwargs)

    @classmethod
    def contains(cls, name: str) -> bool:
        return name in cls._entries()

    @classmethod
    def keys(cls) -> Tuple[str, ...]:
        return tuple(cls._entries().keys())

    @classmethod
    def items(cls) -> Tuple[Tuple[str, T], ...]:
        return tuple(cls._entries().items())

    @classmethod
    def clear(cls) -> None:
        """Remove all entries (useful for tests)."""
        cls._entries().clear()


class AgentRegistry(RegistryBase):
    """Registry for agent classes."""
    pass


class ToolRegistry(RegistryBase):
    """Registry for tool classes."""
    pass


class EngineRegistry(RegistryBase):
    """Registry for LLM engine/provider classes."""
    pass
