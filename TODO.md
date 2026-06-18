Identify question structure via the verifier agent and have a subset of data per question (like a json object per question with keys), using this information:

- Structure: Is it a single question or multiple questions with one scenario? Is it a multiple-choice question or open-ended question? If it’s an open-ended question, how many parts does it contain?
- Language: Is it straight-to-the-point or story-like? (end-of-chapter style vs. content-rich)
- Scenario: Is it with a very abstract scenario? (I think most of the advanced physics questions will be categorized as with abstract scenarios.) If not, is the scenario related to certain student background, such as life sciences (including medical/health-related, biology, chemistry) or engineering (including electric or mechanical); or certain region or city (e.g., we like to use Connecticut/Boston/New York/Highway 84/91 as examples); or modern culture (medieval, D&D, video games, SiFi, sports, etc.)?
- Conceptual/Symbolic/Numerical: Is the question a conceptual question, or a question providing and/or asking for symbolic expressions, or a question only with numbers?
- Concepts and Misconcepts: What are the main concept(s) and fundamental equation(s) the question is built on? If it is a multiple-choice question, can possible misconceptions be suggested (because I feel it is hard for LLM to analyze the distractors for misconceptions) and tested to be aligned with the distractors of the original question?

Make the Evaluator as solid as possible.
