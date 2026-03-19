from llm.client import LLMClient
from agents.base_agent import BaseAgent
from agents.ThinkerAgent import ThinkerAgent
from runtime.agent_runtime import AgentRuntime
from runtime.message_bus import MessageBus
from prompts.prompt_loader import load_prompt


def main():

    # LLM
    llm = LLMClient(model="minimax-m2.5:cloud")

    # Agents
    thinker = ThinkerAgent(
        name="Thinker",
        system_prompt=load_prompt("thinker_prompt.txt"),
        llm=llm
    )

    critic = BaseAgent(
        name="Critic",
        system_prompt=load_prompt("critic_prompt.txt"),
        llm=llm
    )

    # Message Bus
    bus = MessageBus()

    # Runtime
    runtime = AgentRuntime(
        agents={
            "Thinker": thinker,
            "Critic": critic
        },
        message_bus=bus,
        max_turns=10
    )

    # Prompt inicial
    prompt = """
    tarea: Objetivo del Proyecto: Desarrollar un portal interno a medida dentro del ecosistema Shopify, independiente de la tienda online pública (UCAM Store), destinado exclusivamente a que los empleados realicen solicitudes internas de merchandising corporativo para eventos, jornadas y actos institucionales.
        Requisitos Clave del Desarrollo:
            Acceso Restringido: El portal debe contar con autenticación exclusiva para empleados a través de su correo corporativo institucional
            .
            Catálogo Exclusivo: Mostrar un catálogo de productos destinado únicamente a uso interno, totalmente separado del catálogo público
            .
            Proceso tipo "Carrito sin Pago": Las solicitudes funcionarán como pedidos internos, por lo que no debe haber un proceso de pago o cobro asociado
            .
            Recopilación de Datos: Cada solicitud debe recoger información clave: departamento que lo solicita, evento al que va destinado, fecha en la que se necesita, cantidades y observaciones adicionales
            .
            Flujo de Aprobación: Integrar, de ser posible, un sistema para la validación o aprobación interna de dichas solicitudes
            .
            Gestión Centralizada: El sistema debe integrarse manteniendo una administración sencilla desde el back-office actual de Shopify
            .
    """

    runtime.run(prompt)

if __name__ == "__main__":
    main()