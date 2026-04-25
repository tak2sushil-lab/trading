# claude_tools.py — define tools Claude can call, send to Anthropic API

import anthropic, requests, json

client = anthropic.Anthropic()

# Tools Claude knows about — maps to your bridge.py endpoints
tools = [
  {
    "name": "place_order",
    "description": "Buy or sell a US stock via IBKR",
    "input_schema": {
      "type": "object",
      "properties": {
        "symbol": {"type": "string", "description": "Stock ticker e.g. AAPL"},
        "qty":    {"type": "integer", "description": "Number of shares"},
        "side":   {"type": "string", "enum": ["BUY", "SELL"]}
      },
      "required": ["symbol", "qty", "side"]
    }
  },
  {
    "name": "get_portfolio",
    "description": "Get current portfolio positions",
    "input_schema": {"type": "object", "properties": {}}
  },
  {
    "name": "get_quote",
    "description": "Get live price quote for a stock",
    "input_schema": {
      "type": "object",
      "properties": {
        "symbol": {"type": "string"}
      },
      "required": ["symbol"]
    }
  }
]

def call_bridge(tool_name, tool_input):
    # Routes Claude's tool call → your local bridge server
    base = "http://localhost:8000"
    if tool_name == "place_order":
        r = requests.post(f"{base}/place_order", json=tool_input)
    elif tool_name == "get_portfolio":
        r = requests.get(f"{base}/portfolio")
    elif tool_name == "get_quote":
        r = requests.get(f"{base}/quote/{tool_input['symbol']}")
    return r.json()

def chat(user_message):
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            tools=tools,
            messages=messages
        )

        # If Claude wants to call a tool, execute it
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = call_bridge(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            messages += [
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": tool_results}
            ]
        else:
            # Claude has a final answer
            return response.content[0].text

# Example usage:
print(chat("What's the current price of AAPL?"))
print(chat("Buy 5 shares of MSFT at market price"))
print(chat("Show me all my current positions"))