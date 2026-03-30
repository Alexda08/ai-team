import json
from agents.base_agent import BaseAgent
from common.utils import Utils

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["VIABLE", "REWORK"]
        },
        "criteria": {
            "type": "object",
            "properties": {
                "technology_stack": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]},
                "module_boundaries": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]},
                "data_models": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]},
                "operations": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]},
                "dependencies": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]},
                "edge_cases": {"type": "string", "enum": ["CLEAR", "UNCLEAR"]}
            },
            "required": ["technology_stack", "module_boundaries", "data_models", "operations", "dependencies", "edge_cases"]
        },
        "feedback": {
            "type": "string"
        }
    },
    "required": ["status", "criteria", "feedback"]
}


class ArchitectAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    def review_plan(self, plan):
        prompt = f"""ACTION PLAN TO REVIEW:
            {plan}

            You are reviewing this plan BEFORE generating a Blueprint. Your job is to evaluate whether this plan contains enough technical detail for you to make concrete architectural decisions.

            EVALUATE THESE 6 CRITERIA:
            1. TECHNOLOGY STACK: Does the plan specify the language, framework, and key libraries? Can you determine what file types to create?
            2. MODULE BOUNDARIES: Does the plan describe distinct components/modules with clear responsibilities? Can you decide what goes in each file?
            3. DATA MODELS: Does the plan describe what data entities exist, their fields, and relationships? Can you define classes and their attributes?
            4. OPERATIONS: Does the plan describe what operations/features the system must support? Can you derive method signatures from them?
            5. DEPENDENCIES: Does the plan make clear which components depend on which? Can you determine a safe implementation order?
            6. EDGE CASES: Does the plan mention error handling, validation rules, or boundary conditions? Can you decide where error handling goes?

            SCORING:
            - For each criterion, decide: CLEAR (enough info to architect) or UNCLEAR (missing info, would force you to guess)
            - If 5-6 criteria are CLEAR → status is VIABLE
            - If 3-4 criteria are CLEAR → status is REWORK (minor improvements needed)
            - If 0-2 criteria are CLEAR → status is REWORK (major rework needed)

            FEEDBACK FIELD:
            - If VIABLE: one sentence noting any minor gaps you will handle in the Blueprint
            - If REWORK: specific list of what is missing or unclear — be concrete about what must be added, do not ask generic questions

            Return ONLY valid JSON.
        """

        raw = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=REVIEW_SCHEMA
        )

        return self._parse_review(raw)

    def _parse_review(self, raw):
        try:
            result = json.loads(Utils.clean_json(raw))

            if result.get("status") not in ("VIABLE", "REWORK"):
                return {"status": "REWORK", "criteria": {}, "feedback": "Invalid status in response."}

            return {
                "status": result["status"],
                "criteria": result.get("criteria", {}),
                "feedback": result.get("feedback", "")
            }

        except (json.JSONDecodeError, KeyError) as e:
            return {"status": "REWORK", "criteria": {}, "feedback": f"Failed to parse review: {e}"}

    def generate_bluePrint(self, plan):
        prompt = f"""ACTION PLAN TO ARCHITECT:
            {plan}

            Analyze this plan and produce a complete PROJECT BLUEPRINT.

            For every feature and step described in the plan, you must decide:
            1. What files need to exist and where
            2. What classes and functions go in each file
            3. The exact method signatures with parameter types and return types
            4. What each file imports from other project files
            5. The safe implementation order (dependency-free files first)

            MANDATORY SECTIONS (your system prompt has the full format):

            1. CONVENTIONS — Language-specific rules that ALL coders must follow:
               - Export/import style (pick ONE convention, enforce everywhere)
               - Constructor naming (constructor for JS, __init__ for Python)
               - Async pattern, string quotes, semicolons, file naming
               - Use the TARGET LANGUAGE idioms, not Python idioms in JS or vice versa

            2. INTEGRATION CONTRACTS — If the project has IPC, API routes, events, or any cross-file communication:
               - Define EVERY channel/route/event name as an exact string
               - Create a shared constants file that both sender and receiver import
               - NEVER allow hardcoded strings in multiple files
               
            3. CONSTRUCTOR REGISTRY — For the entry point file:
               - List EVERY class instantiation in dependency order
               - Show exact constructor parameters
               - This is the wiring diagram — the entry point coder copies this verbatim

            4. FILES — Each file must include:
               - exports: the EXACT export statement (e.g. "module.exports = {{ ClassName }}")
               - Constructor signature with exact parameters
               - All method signatures

            5. INTEGRATION TASKS — List what needs dedicated integration tasks:
               - Entry point wiring
               - IPC/API channel unification
               - Cross-module contract verification

            CRITICAL REQUIREMENTS:
            - Every CONSTRUCTOR must list its exact parameters — this is the most important signature
            - Every method must have a complete signature: name(param: type, param: type) -> return_type
            - Every method must be marked [NEW] or [MODIFY]
            - Every file must have a complexity hint and an exports line
            - Dependencies between files must be explicit import statements with exact syntax
            - The DEPENDENCY ORDER must allow sequential implementation with no blocking

            Do NOT skip any feature from the plan. Every step in the plan must map to at least one file.
            Do NOT write implementation code — only structure and signatures.

            Produce the full PROJECT BLUEPRINT now.
        """

        return self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )