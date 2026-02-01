from pathlib import Path

from agents import CodeInterpreterTool, Agent, ModelSettings, TResponseInputItem, Runner, RunConfig, trace
from agents.logger import logger
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel

# Tool definitions
code_interpreter = CodeInterpreterTool(tool_config={
  "type": "code_interpreter",
  "container": {
    "type": "auto",
    "file_ids": [

    ]
  }
})
PROJECT_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = PROJECT_ROOT / "Prompts"


def load_prompt(name: str) -> str:
  prompt_path = PROMPTS_DIR / f"{name}.txt"
  if not prompt_path.exists():
    logger.warning("Prompt file not found: %s", prompt_path)
    return ""
  try:
    return prompt_path.read_text(encoding="utf-8")
  except Exception as exc:
    logger.warning("Failed to load prompt %s: %s", name, exc)
    return ""

class OptionItem(BaseModel):
  key: str
  text: str
  is_correct: bool
  explanation: str


class SourceQuestionItem(BaseModel):
  source_id: float
  source_raw: str
  stem: str
  options: list[OptionItem]
  has_explicit_answer: bool
  difficulty: str
  question_type: str
  topic_tags: list[str]

class NewQuestionItem(BaseModel):
  id: float
  source_id: float
  stem: str
  options: list[OptionItem]
  difficulty: str
  topic_tags: list[str]
  generation_notes: str


class GenerationSchema(BaseModel):
  isomorphic_questions: list[NewQuestionItem]


class ExtracterSchema(BaseModel):
  questions: list[SourceQuestionItem]


class VerifierSchema(BaseModel):
  questions: list[SourceQuestionItem]


class NormalizerSchema(BaseModel):
  new_questions_per_question: float
  difficulty: str
  theme: str
  title: str
  notes: str


class PythonSchema(BaseModel):
  pass


normalizer = Agent(
  name="Normalizer",
  instructions=load_prompt("normalizer"),
  model="gpt-5.2",
  output_type=NormalizerSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="none",
      summary="auto"
    )
  )
)


extracter = Agent(
  name="Extracter",
  instructions=load_prompt("extracter"),
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=ExtracterSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


generator = Agent(
  name="Generator",
  instructions=load_prompt("generator"),
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=GenerationSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="medium",
      summary="auto"
    )
  )
)


verifier = Agent(
  name="Verifier",
  instructions=load_prompt("verifier"),
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  output_type=VerifierSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


latex = Agent(
  name="LaTeX",
  instructions=load_prompt("latex"),
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="medium",
      summary="auto"
    )
  )
)


downloader = Agent(
  name="Downloader",
  instructions=load_prompt("downloader"),
  model="gpt-5.2",
  tools=[
    code_interpreter
  ],
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


python = Agent(
  name="Python",
  instructions=load_prompt("python"),
  model="gpt-5.2",
  output_type=PythonSchema,
  model_settings=ModelSettings(
    store=True,
    reasoning=Reasoning(
      effort="low",
      summary="auto"
    )
  )
)


class WorkflowInput(BaseModel):
  input_as_text: str


# Main code entrypoint
async def run_workflow(workflow_input: WorkflowInput):
  with trace("Isomorphic Physics Question Generation Agent - Modified"):
    workflow = workflow_input.model_dump()
    conversation_history: list[TResponseInputItem] = [
      {
        "role": "user",
        "content": [
          {
            "type": "input_text",
            "text": workflow["input_as_text"]
          }
        ]
      }
    ]
    normalizer_result_temp = await Runner.run(
      normalizer,
      input=[
        *conversation_history,
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Defaults unless specified otherwise:
              New questions per question is one-to-one. Difficulty remains the same.
              Theme remains the same. Title is 'Isomorphic Questions' unless one is extracted from the input source.

             {workflow["input_as_text"]}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )
    conversation_history.extend([item.to_input_item() for item in normalizer_result_temp.new_items])
    normalizer_output = normalizer_result_temp.final_output
    user_config_obj = normalizer_output.model_dump()
    user_config_json = normalizer_output.json()
    extracter_result_temp = await Runner.run(
      extracter,
      input=[
        *conversation_history,
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Extract all information from any provided documents, text, images, etc.. 
              Organize any additional information into structured data.

             {workflow["input_as_text"]}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )
    conversation_history.extend([item.to_input_item() for item in extracter_result_temp.new_items])
    extracter_output = extracter_result_temp.final_output
    questions_obj = extracter_output.model_dump()
    questions_json = extracter_output.json()
    verifier_result_temp = await Runner.run(
      verifier,
      input=[
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""{questions_json}

            Verify all the answers for these questions are correct. If any answers are missing, generate the correct answer. If any are not multiple choice, remove the question entirely."""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )
    conversation_history.extend([item.to_input_item() for item in verifier_result_temp.new_items])
    verifier_output = verifier_result_temp.final_output
    questions_obj = verifier_output.model_dump()
    questions_json = verifier_output.json()
    generation_result_temp = await Runner.run(
      generator,
      input=[
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Generate new questions according to configuration. 
            {user_config_json}"""
            }
          ]
        },
        {
          "role": "user",
          "content": [
            {
              "type": "input_text",
              "text": f"""Question data:

             {questions_json}"""
            }
          ]
        }
      ],
      run_config=RunConfig(trace_metadata={
        "__trace_source__": "agent-builder",
        "workflow_id": "wf_6945e6c1d1e48190a4eed31ef49d60200258ba102ae1ea2b"
      })
    )
    conversation_history.extend([item.to_input_item() for item in generation_result_temp.new_items])
    return generation_result_temp.final_output.model_dump()
