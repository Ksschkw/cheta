# How I gave my chatbot a memory that outlived its own database

Most chatbots forget you when the conversation ends. A support bot asks for your
order number for the third time. That repetition is what I wanted to solve, so I
built an assistant whose memory survives not just the conversation but the server
it runs on.

## What I built

Cheta is a personal assistant you can talk to from Telegram, a web page, a command
line client, or a browser extension. It is for anyone tired of explaining
themselves twice. Its purpose is narrow: hold onto the durable facts a person
tells it, and bring them back when they matter.

## How the memory works

Walrus Memory is a storage service that keeps data as blobs on the Walrus network,
which is a decentralised store rather than a database tied to one server. Your
application talks to it through a relayer, an HTTP service that accepts writes and
answers recalls.

Four things happen on every message.

Cheta reads the message and asks the relayer to recall anything related to it.
The relayer does a semantic search, so it finds notes by meaning rather than by
keyword. Whatever comes back is inserted into the prompt as context before the
model sees the message. That is the whole trick at the point of use: recalled
facts become part of the question.

Then the model's reply is shown, and the turn is passed to an extractor. The
extractor pulls out facts worth keeping, things like a preference, a constraint,
or a fact about someone's work. Each one is written to the relayer under a
namespace derived from the person, so one person's memory never mixes with
another's.

The last step is where the project stopped being a demo for me. Before writing a
new fact, Cheta checks it against what it already knows. A restatement is dropped.
A changed preference retires the older note rather than sitting beside it. Two
facts that conflict are stored and flagged, so the assistant says so out loud
instead of holding both and contradicting itself later.

## The before and after

Without memory the assistant answers every message in isolation. Ask it for a
restaurant recommendation and it suggests anything, including a dish full of an
ingredient you told it last week you cannot eat.

With memory the same question produces a different answer, and I wanted to prove
that rather than assert it. Cheta stores each turn, so it can re-run the same turn
with memory switched off and put the two answers side by side. The model, the
question and the moment are identical. The only difference is whether the recalled
facts were in the prompt. On one of my own turns the reply with memory named a
stored seat preference; without memory it said it had no such information on file.
That comparison is the most useful thing I built, because it turns "does memory do
real work" into something you can look at.

## What broke

The failures are worth more to you than the successes.

My first version kept the local index in a SQLite file on the server. Every
redeploy wiped it, so the assistant told a person with twenty stored facts that
it had nothing about them. The facts were safe the whole time. The index was a
cache, and I had forgotten that. It now rebuilds from a snapshot stored in Walrus
itself, so a fresh server recovers what it lost.

Consolidation barely worked at first. An exact duplicate could be stored twice,
because my comparison ran on the raw text and two notes phrased differently
("The user is a student" and a version using a person's name) never matched. Both
stayed active. Comparing a normalised form fixed it.

The worst was latency. Turns took up to forty seconds, and the cause was not the
model. The provider was rate limiting, and the client library's automatic retry
was sleeping for up to fifty seconds before trying again, against a timeout of
thirty. Every retry was wasted work. Bounding the retry and rotating across
several provider keys brought it down.

## Evidence and code

Three people used it, with nineteen, seventeen and twenty stored memories each,
which the repository's evidence page reads live from the deployment.

The code is at https://github.com/Ksschkw/cheta, with setup instructions in the
README. It runs without any paid service: memory on Walrus, an open-weight model
for replies, and an offline model so you can clone it and watch the memory
behaviour before configuring anything.

If you are adding memory to your own bot, the lesson I would pass on is this.
Storing facts is the easy part. Deciding which ones to keep, and proving the
difference they make, is the part that changes how the thing feels to use.
