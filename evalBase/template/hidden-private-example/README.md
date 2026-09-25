This directory shows the *shape* of the private hidden-generator tree: one
`gen/<family>.py` per public family module, each exposing
`generate(split, corpus)`. In a real evaluation it is a separate private
repository, named by `CorpusSpec.hidden_env` (default: a sibling directory
`<instance>-hidden`), and the loader refuses it if it lies inside the public
tree. Delete this example once your private tree exists.
