# Spec: log raw LLM response in validators

Problem: validators have no raw-response logging. When a CF model 
returns empty/unparseable content, we're blind. The coder already 
logs raw output at litellm.py:374-378 — mirror that.

In validators/llm_validator.py, in _call_llm and _call_llm_structured 
(around lines 555-573), log the raw response content at WARNING level 
before parsing, matching the coder's pattern:

  _logger.warning("Validator %s raw response (first 2KB): %s", 
                  self.validator_id, _truncated_log(content))

Use the same _truncated_log helper the coder uses (import or replicate).

Touch only: validators/llm_validator.py

Commit: feat(validators): log raw LLM response for debugging
