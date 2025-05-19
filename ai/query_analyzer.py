from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_openai import OpenAI

def analyze_query(user_input: str):
    llm = OpenAI(model_name="gpt-4")
    prompt = PromptTemplate(
        input_variables=["query"],
        template="Analyze this crypto trading query and extract: 1) Coin/token mentioned, 2) Time frame, 3) Trading action requested: {query}"
    )
    chain = LLMChain(llm=llm, prompt=prompt)
    return chain.run(user_input)