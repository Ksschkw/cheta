# How I gave my chatbot a memory that outlived its own database

Most chatbots forget you when the conversation ends. A support bot asks for your
order number for the third time. You explain your setup again to a tool that had
it last week. I built an assistant called Cheta to fix that, and then kept going,
because remembering turned out to be the easier half.

## What it does

Cheta is an assistant you can talk to from Telegram, a web page, a command line
client, or a browser extension. All four share one memory space per person.

It answers with tools, not only with text. I gave it a web search, a page
crawler, a URL reader, Wikipedia, live weather, an exact calculator, reminders
that fire later, and a calendar file generator. It reads documents: PDF, Word,
PowerPoint including the speaker notes, Excel, and any plain text or source file,
detected by content rather than by file extension. It transcribes voice notes and
shows me what it heard before it answers. The browser extension can read the page
I am looking at and act on it, and if that site publishes public MCP tools, which
is a standard for exposing functions to an assistant, it can call those too.

## What memory means here

Walrus Memory is a storage service that keeps data as blobs on the Walrus
network. An application reaches it through a relayer, which is an HTTP service
that accepts writes and answers recalls.

On every message Cheta asks the relayer to recall anything related. That search
is semantic, so it finds notes by meaning rather than by keyword. The results go
into the prompt before the model sees my message. After the reply, an extraction
pass decides what is worth keeping, and before writing anything it checks the new
fact against what it already has. A restatement is dropped. A changed preference
retires the older note instead of sitting beside it. Two facts that conflict are
flagged, so it can tell me rather than contradict itself a week later.

## The before and after

Without memory the assistant answers every message in isolation. Ask it for a
restaurant and it suggests anything, including a dish full of an ingredient you
told it you cannot eat.

I did not want to assert that memory helped. I wanted to show it, so Cheta stores
each turn and can re-run the same turn with memory switched off. Same model, same
question, same minute. On one of my own turns the answer with memory named a
stored seat preference, and the answer without it said it had no such information
on file. Putting those two side by side is the most useful thing I built.

## What broke

The failures taught me more than the features did.

My first version kept its index in a SQLite file on the server. Every redeploy
erased it, so the assistant told a person with twenty stored facts that it had
nothing about them. The facts were safe the whole time, on Walrus, because the
local index was only a cache and I had forgotten that. It now rebuilds from a
snapshot held in Walrus itself.

Consolidation barely worked at first. An exact duplicate could be stored twice,
because my comparison ran on raw text and two notes phrased differently never
matched. Comparing a normalised form fixed it.

The worst was latency. Turns took up to forty seconds, and the cause was not the
model. The provider was rate limiting and the client library's automatic retry
was sleeping for up to fifty seconds before trying again, against a timeout of
thirty. Every retry was wasted work. Disabling that retry and rotating across
several API keys brought it down.

## Evidence

Three people used it, holding nineteen, seventeen and twenty stored memories.

The code is at https://github.com/Ksschkw/cheta, with setup instructions in the
README. It runs without any paid service, using an open-weight model through
Groq, with a deterministic offline model so you can clone it and watch the memory
behaviour before configuring anything.

If you are adding memory to your own bot, the lesson I would pass on is this.
Storing facts is the easy part. Deciding which ones to keep, acting on them, and
proving the difference they make is the part that changes how the thing feels to
use.
