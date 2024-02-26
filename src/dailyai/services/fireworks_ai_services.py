import aiohttp
from PIL import Image
import io
from openai import AsyncOpenAI

import asyncio
import json
from collections.abc import AsyncGenerator

from dailyai.services.ai_services import LLMService, ImageGenService

from dailyai.queue_frame import (TextQueueFrame, TextQueueOutOfBandFrame)


class FireworksLLMService(LLMService):
    def __init__(self, *, api_key, model="", tools=[], context, change_appearance, transport=""):
        super().__init__(context)
        self._model = model
        self._tools = tools
        self._change_appearance = change_appearance
        self._transport = transport
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.fireworks.ai/inference/v1"
        )

    async def get_response(self, messages, stream):
        print("GET RESPONSE ... WHEN DO WE EXPECT THIS TO BE CALLED?")
        return await self._client.chat.completions.create(
            stream=stream,
            messages=messages,
            model=self._model,
            temperature=0.1,
            tools=self._tools
        )

    async def run_llm_async(self, messages) -> AsyncGenerator[str, None]:
        print("IN ASYNC")
        messages_for_log = json.dumps(messages)
        self.logger.debug(f"Generating chat via openai: {messages_for_log}")

        chunks = await self._client.chat.completions.create(
            model=self._model,
            stream=True,  # BLARGH
            messages=messages,
            temperature=0.1,
            tools=self._tools
        )

        tool_call = {}

        async for chunk in chunks:
            print(f"CHUNK: {chunk}")
            if len(chunk.choices) == 0:
                continue

            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

            if chunk.choices[0].delta.tool_calls:
                print(f"TOOL CALLS: {chunk.choices[0].delta.tool_calls[0]}")
                if chunk.choices[0].delta.tool_calls[0].function.name:
                    tool_call["id"] = chunk.choices[0].delta.tool_calls[0].id
                    tool_call["name"] = chunk.choices[0].delta.tool_calls[0].function.name
                    tool_call["arguments"] = ''
                if chunk.choices[0].delta.tool_calls[0].function.arguments:
                    tool_call["arguments"] += chunk.choices[0].delta.tool_calls[0].function.arguments

            if chunk.choices[0].finish_reason:
                print(f"TOOL CALLS ACCUM -- {tool_call}")
                if tool_call.get("name"):
                    # hard coding tool call action for now. we should assemble the tool call
                    # from the streaming response, then yield it to the pipeline.
                    # this approach works for the first few change appearance requests but
                    # then the model starts refusing. need to read more about function
                    # calling, try this with the OpenAI APIs, and talk to the Fireworks people.
                    self._transport.append_to_context("assistant", {
                        # pipeline will append the content to this context after it goes
                        # through tts. we need to manually append the tool call, though
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tool_call["id"],
                                "type": "function",
                                "index": 0,
                                "function": {
                                    "name": tool_call["name"],
                                    "arguments": tool_call["arguments"]
                                },
                            }
                        ],
                    })
                    self._transport.append_to_context("tool", {
                        "content": "image generated by prompt arguments: " + tool_call["arguments"],
                        "role": "tool",
                        "tool_call_id": tool_call["id"]
                    })
                    self._transport.append_to_context("assistant", {
                        "content": f"call to {tool_call['name']} function succeeded",
                        "role": "assistant",
                    })
                    print("APPENDED TO CONTEXT")
                    image_prompt = json.loads(
                        tool_call["arguments"]).get("appearance")
                    print("IMAGE PROMPT", image_prompt)
                    asyncio.create_task(
                        self._change_appearance(image_prompt))
                    yield TextQueueOutOfBandFrame("Sure, let me work on that for you!")
                    # yield {"content": "Sure, let me work on that for you!"}
                    # yield "Sure, let me work on that for you!"

    async def run_llm(self, messages) -> str | None:
        print("--> IN SYNC ... WHEN DO WE EXPECT THIS TO BE CALLED?")
        messages_for_log = json.dumps(messages)
        self.logger.debug(f"Generating chat via openai: {messages_for_log}")

        response = await self._client.chat.completions.create(model=self._model, stream=False, messages=messages)
        if response and len(response.choices) > 0:
            return response.choices[0].message.content
        else:
            return None
