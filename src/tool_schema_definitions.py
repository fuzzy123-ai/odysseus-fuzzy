"""Built-in OpenAI-compatible function tool schema definitions."""

FUNCTION_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Delegate a focused read-only analysis subtask to an isolated worker agent. The worker receives bounded provider context and returns compact JSON; it does not mutate host files, create files, run GUI/browser checks, execute tests, or keep conversation history. Do not use this for implementation tasks such as creating pong.py; use sandbox/coding tools instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The concrete subtask for the worker agent."},
                    "context_query": {"type": "string", "description": "Optional query used to preload relevant provider context."},
                    "budget": {"type": "integer", "description": "Approximate provider-context token budget for the worker."}
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": "Create a durable subagent run using the fake runtime backend only. Use for scoped long-lived worker orchestration; this does not send to live threads, run shell commands, or call providers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "Plan or roadmap id."},
                    "node_id": {"type": "string", "description": "Plan node id for this slice."},
                    "slice_id": {"type": "string", "description": "Slice id such as SUB2-spawn-api."},
                    "agent_id": {"type": "string", "description": "Scoped agent id, for example alice or bob."},
                    "role_id": {"type": "string", "description": "Role id for the agent."},
                    "objective": {"type": "string", "description": "Concrete scoped objective for the run."},
                    "allowed_files": {"type": "array", "items": {"type": "string"}, "description": "Repo-relative files allowed for the fake run contract."},
                    "blocked_files": {"type": "array", "items": {"type": "string"}, "description": "Repo-relative files blocked for the fake run contract."},
                    "tests": {"type": "array", "items": {"type": "string"}, "description": "Expected focused tests as declarations, not commands to execute."},
                    "handoff_format": {"type": "array", "items": {"type": "string"}, "description": "Required handoff fields."},
                    "evidence_required": {"type": "array", "items": {"type": "string"}, "description": "Evidence expected before verified done."},
                    "target_kind": {"type": "string", "enum": ["job", "thread"], "description": "Fake target kind; thread still means fake ThreadRef only."}
                },
                "required": ["plan_id", "node_id", "slice_id", "agent_id", "objective", "allowed_files"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_subagents",
            "description": "Inspect or control fake-backend subagent runs. Actions are list, snapshot, status, pause, resume, cancel, retry, or read. No live threads or shell commands are used.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "snapshot", "status", "pause", "resume", "cancel", "retry", "read"]},
                    "agent_run_id": {"type": "string", "description": "Required for status, pause, resume, cancel, retry, and read."},
                    "plan_id": {"type": "string", "description": "Required for snapshot."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command (full access). Prefer a dedicated tool whenever one fits the job (reading, writing, editing, searching, or listing files); use bash only for what no dedicated tool covers (installs, git, builds, running programs, system info). Do NOT create or edit files via bash redirects/heredocs/sed -- use the dedicated file tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python code to compute a result or test something. Prefer a dedicated tool whenever one fits the job (reading, writing, or searching files); use python only for computation, data processing, or scripting no dedicated tool covers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Quick single web lookup for a fact or current event mid-task. NOT for 'research X' / 'do research on X' — those are deep-research jobs; use trigger_research instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Optional freshness filter for news/latest/today queries"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and read the text content of a specific URL the user names (e.g. 'check example.com', 'what's on this page <url>'). Use when you already have a concrete URL/domain. NOT for open-ended searches (use web_search) or 'research X' jobs (use trigger_research). Downloads are size-budgeted; a '[partial content: ...]' notice in the result means the body was cut short and you can re-call with full=true for the rest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL or domain to fetch (http/https; a bare domain like example.com is fine)"},
                    "full": {"type": "boolean", "description": "Raise the download budget to the hard cap for large pages/files. Use only after a result reported partial content."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk. Optionally read a line range with offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "1-based line to start reading from (optional)"},
                    "limit": {"type": "integer", "description": "Max number of lines to read from offset (optional)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regular expression across a directory tree (uses ripgrep when available, respecting .gitignore). Returns file:line:match. PREFER this over `bash grep/rg` for code search — confined to the allowed roots, structured output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for"},
                    "path": {"type": "string", "description": "Directory or file to search (optional; defaults to the project root)"},
                    "glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py' (optional)"},
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive match (optional)"},
                    "max_results": {"type": "integer", "description": "Max matches to return (optional)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern (recursive), newest first. e.g. '**/*.py'. PREFER this over `bash find/ls` for locating files — confined to the allowed roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.ts' or 'src/**/test_*.py'"},
                    "path": {"type": "string", "description": "Base directory (optional; defaults to the project root)"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List the entries of a directory (folders first, then files with sizes). PREFER this over `bash ls` — confined to the allowed roots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list (optional; defaults to the project root)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_workspace",
            "description": "Return the absolute path of the active workspace folder the user is working in. File tools are confined to it; the shell starts there but is not sandboxed. Call this first when the user refers to 'the project'/'the code'/'this folder' without a path, instead of asking them. Takes no arguments.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write/save a file to disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to"},
                    "content": {"type": "string", "description": "File content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file ON DISK by exact string replacement (home folder, project files, any real path like ~/sweden.txt or /path/to/file). This is the right tool for files on disk — NOT edit_document (that's for editor-panel documents). PREFER this over bash (sed/echo) — it shows a diff. old_string must match the file exactly and be unique (or set replace_all). Use write_file to create a new file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Exact text to replace (must match the file, including indentation)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring a unique match"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a new document in the editor panel. Use this when the user asks to write, create, build, or generate code, scripts, programs, games, apps, or any substantial content (>15 lines) AND there is no already-open document/email draft that the request refers to. If an email compose draft is open, edit that draft instead of creating another document. NEVER put large code blocks directly in chat — use this tool instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title"},
                    "language": {"type": "string", "description": "Programming language or format (e.g. python, javascript, markdown, text)"},
                    "content": {"type": "string", "description": "The document content"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_document",
            "description": "Edit a document OPEN IN THE EDITOR PANEL (created via create_document) — NOT a file on disk. For files on disk (home folder, project files, anything with a path like ~/x.txt or /path/to/file) use edit_file instead. Targeted find-and-replace with multiple FIND/REPLACE pairs per call; use for any edit smaller than a full rewrite. Do NOT send the whole file back via update_document for small edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": "List of find/replace edits (first match only per edit)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text to find in the document"},
                                "replace": {"type": "string", "description": "Text to replace it with"}
                            },
                            "required": ["find", "replace"]
                        }
                    }
                },
                "required": ["edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_document",
            "description": "Suggest improvements to the active document WITHOUT editing it. Creates inline comment bubbles the user can accept or reject. Use when the user asks for suggestions, review, improvements, or feedback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestions": {
                        "type": "array",
                        "description": "List of suggested changes with reasons",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Exact text in the document to suggest changing"},
                                "replace": {"type": "string", "description": "Suggested replacement text"},
                                "reason": {"type": "string", "description": "Brief explanation of why this change helps"}
                            },
                            "required": ["find", "replace", "reason"]
                        }
                    }
                },
                "required": ["suggestions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_document",
            "description": "Replace the ENTIRE active document. ONLY use for genuine full rewrites (>50% of lines changed). For any smaller change, use edit_document — echoing back the whole file for small edits is wasteful.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Complete new document content"}
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_chats",
            "description": "Search the user's past session transcripts by keyword. Use when the user asks about previous chats, past conversations, or when direct transcript evidence is better than persistent memory. Returns matching sessions with clickable links and nearby context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword(s) to find in past conversations"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_with_model",
            "description": "Send a message to another AI model and get its response. Use for getting a second opinion, delegating subtasks, or AI-to-AI communication.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Model name (e.g. 'qwen3-32b') or model@endpoint_name"},
                    "message": {"type": "string", "description": "The message to send to the model"}
                },
                "required": ["model", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_session",
            "description": "Create a new chat for ongoing conversations with a specific model. (The UI calls these 'chats'; 'session' is the internal term.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new chat"},
                    "model": {"type": "string", "description": "Model name or model@endpoint_name"}
                },
                "required": ["name", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_sessions",
            "description": "List the user's chats (the UI calls them 'chats') as clickable markdown links. Use this to enumerate chats before opening, renaming, archiving, or deleting them. When replying to the user, preserve the returned [title](#session-id) links; do not strip them into plain text. Optionally filter by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter chats by name"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_to_session",
            "description": "Send a message to an existing chat and get the model's response. The chat keeps its conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "The id of the chat to send the message to"},
                    "message": {"type": "string", "description": "The message to send"}
                },
                "required": ["session_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pipeline",
            "description": "Run a multi-step AI pipeline where each model's output feeds the next. Example: Draft -> Critique -> Revise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "Pipeline steps in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "model": {"type": "string", "description": "Model name for this step"},
                                "instruction": {"type": "string", "description": "What this step should do"}
                            },
                            "required": ["model", "instruction"]
                        }
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_session",
            "description": "Manage a chat: rename, archive, unarchive, delete, mark important, truncate history, or fork it. (The UI calls these 'chats'; 'session' is the internal term.) For destructive actions like delete/truncate, call list_sessions first, pass the exact id returned there, and set confirmed=true after explicit user confirmation; never invent ids.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["rename", "archive", "unarchive", "delete", "important", "unimportant", "truncate", "fork"],
                               "description": "The action to perform"},
                    "session_id": {"type": "string", "description": "Exact target chat id from list_sessions, or 'current' for the active chat where supported"},
                    "value": {"type": "string", "description": "Action parameter: new name (rename), keep_count (truncate/fork)"},
                    "confirmed": {"type": "boolean", "description": "Required true for delete/truncate after explicit user confirmation."}
                },
                "required": ["action", "session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "Manage the user's memory system: list, add, edit, delete, or search memories. Memories persist across sessions. Delete requires confirmed=true after explicit user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"],
                               "description": "The action to perform"},
                    "text": {"type": "string", "description": "Memory text (for add/edit) or search query (for search)"},
                    "memory_id": {"type": "string", "description": "Memory ID (for edit/delete)"},
                    "category": {"type": "string", "enum": ["fact", "event", "contact", "preference"],
                                 "description": "Memory category (for add/list filter)"},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": "List all available AI models across configured endpoints. Optionally filter by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "description": "Optional keyword to filter models"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ui_control",
            "description": "Control the user interface. Actions: toggle (turn tools on/off), open_panel (open a modal: documents/library, gallery, email, sessions, notes, memories/brain, skills, settings, cookbook), open_email_reply (open an email reply draft document; does NOT send), set_mode, switch_model, set_theme (built-in presets: dark, light, midnight, paper, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, organs, lavender, gpt, claude, cute), create_theme (CREATE any custom theme with a name + colors object — pick distinctive, evocative hex colors that match the requested aesthetic, NOT generic defaults. The theme auto-applies after creation). When a user asks for ANY theme not in the built-in preset list, ALWAYS use create_theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["toggle", "open_panel", "open_email_reply", "set_mode", "switch_model", "set_theme", "create_theme", "get_toggles"],
                               "description": "The UI action. Use set_theme for presets, create_theme to build a custom theme with any hex colors"},
                    "name": {"type": "string", "description": "For toggle: web, bash, research, incognito, document_editor (aliases: shell, search, deepresearch, documents). For open_panel: documents, gallery, email, sessions, notes, brain/memories, skills, settings, cookbook. For open_email_reply: email UID. For set_theme: a preset theme name. For create_theme: the custom theme name."},
                    "value": {"type": "string", "description": "Value: on/off for toggle, agent/chat for set_mode, model name for switch_model, theme name for set_theme, or folder for open_email_reply"},
                    "uid": {"type": "string", "description": "Email UID for open_email_reply"},
                    "folder": {"type": "string", "description": "Email folder for open_email_reply (default INBOX)"},
                    "mode": {"type": "string", "description": "Reply draft mode for open_email_reply: reply, reply-all, or ai-reply"},
                    "colors": {"type": "object", "description": "For create_theme: the theme colors",
                               "properties": {
                                   "bg": {"type": "string", "description": "Background color (hex, e.g. #1a1a2e)"},
                                   "fg": {"type": "string", "description": "Foreground/text color (hex)"},
                                   "panel": {"type": "string", "description": "Panel/sidebar background color (hex)"},
                                   "border": {"type": "string", "description": "Border/divider color (hex)"},
                                   "accent": {"type": "string", "description": "Accent color for buttons, brand, highlights (hex)"},
                                   "userBubbleBg": {"type": "string", "description": "User chat bubble background (hex, optional)"},
                                   "aiBubbleBg": {"type": "string", "description": "AI chat bubble background (hex, optional)"},
                                   "bubbleBorder": {"type": "string", "description": "Chat bubble border color (hex, optional)"},
                                   "sidebarBg": {"type": "string", "description": "Sidebar background override (hex, optional)"},
                                   "sectionAccent": {"type": "string", "description": "Section header accent color (hex, optional)"},
                                   "brandColor": {"type": "string", "description": "Brand/logo color (hex, optional)"},
                                   "inputBg": {"type": "string", "description": "Chat input background (hex, optional)"},
                                   "inputBorder": {"type": "string", "description": "Chat input border (hex, optional)"},
                                   "sendBtnBg": {"type": "string", "description": "Send button background (hex, optional)"},
                                   "sendBtnHover": {"type": "string", "description": "Send button hover color (hex, optional)"},
                                   "codeBg": {"type": "string", "description": "Code block background (hex, optional)"},
                                   "codeFg": {"type": "string", "description": "Code block text color (hex, optional)"},
                                   "toggleBg": {"type": "string", "description": "Toggle switch off background (hex, optional)"},
                                   "toggleActive": {"type": "string", "description": "Toggle switch on color (hex, optional)"},
                                   "accentPrimary": {"type": "string", "description": "Primary accent override (hex, optional)"},
                                   "accentError": {"type": "string", "description": "Error/danger color (hex, optional)"}
                               },
                               "required": ["bg", "fg", "panel", "border", "accent"]}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a multiple-choice question to get a decision or clarification when the task is genuinely ambiguous and the answer changes what you do next (e.g. pick between approaches, confirm an assumption, choose a target). The user sees clickable option buttons; calling this ENDS your turn and their selection arrives as your next message. Prefer sensible defaults over asking — only ask when you truly cannot proceed well without the user's input. Do NOT use it to confirm irreversible/destructive actions that have a dedicated confirmation flow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "description": "Optional. Use `odysseus.clarification_request.v2` for structured multi-question clarification."},
                    "scope": {"type": "string", "enum": ["conversation", "project", "coding_task"], "description": "V2 clarification scope."},
                    "intent_summary": {"type": "string", "description": "V2 bounded statement of what is currently understood."},
                    "questions": {"type": "array", "description": "V2 questions for project/coding intake and multi-question clarification."},
                    "batch": {"type": "object", "description": "V2 visible batch metadata."},
                    "defaults_visible": {"type": "boolean", "description": "V2: true when recommended defaults are visible but not yet accepted."},
                    "question": {"type": "string", "description": "The question to ask. Be specific and self-contained."},
                    "options": {
                        "type": "array",
                        "description": "2-6 choices. Each is an object with a short `label` and an optional `description` explaining the trade-off.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Concise choice text the user clicks (1-5 words)."},
                                "description": {"type": "string", "description": "Optional one-line explanation of this choice."}
                            },
                            "required": ["label"]
                        }
                    },
                    "multi": {"type": "boolean", "description": "Set true ONLY when the question explicitly allows choosing more than one option. Otherwise omit it or set false. Default false."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Write back to the ACTIVE PLAN: mark steps done or revise them. Use this while executing an approved plan — after you finish a step, call update_plan with the full checklist and that step marked `- [x]`; when the user asks to change the plan, call it with the revised checklist. The user's docked plan window updates live. Pass the COMPLETE checklist every time (not a diff). No effect if there is no active plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string", "description": "The full updated plan as a GitHub-style markdown checklist — one step per line, `- [ ]` for pending and `- [x]` for done. Always send the whole list."}
                },
                "required": ["plan"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Manage scheduled/automated tasks: list, create, edit, delete, pause, resume, or run tasks. Use this for ANY recurring/scheduled request ('every morning…', 'each day at 7:30', 'daily summarize…') — create a task rather than doing it once. Task deletion requires confirmed=true after explicit user confirmation. Task types: llm (AI runs a prompt), research (runs the deep-research pipeline on a question), or action (built-in automation). Triggers can be time-based or event-based.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "edit", "delete", "pause", "resume", "run"],
                               "description": "The action to perform"},
                    "task_id": {"type": "string", "description": "Task ID (for edit/delete/pause/resume/run)"},
                    "name": {"type": "string", "description": "Task name"},
                    "prompt": {"type": "string", "description": "The instruction (for task_type=llm) or the research question (for task_type=research). Required for both."},
                    "task_type": {"type": "string", "enum": ["llm", "research", "action"],
                                  "description": "llm = AI runs your prompt; research = runs the deep-research pipeline on the prompt as a question; action = direct built-in function"},
                    "action_name": {"type": "string", "enum": [
                        "tidy_sessions", "tidy_documents", "consolidate_memory", "tidy_research",
                        "summarize_emails", "draft_email_replies", "extract_email_events",
                        "classify_events", "learn_sender_signatures",
                        "test_skills", "audit_skills", "check_email_urgency"
                    ],
                                    "description": "Built-in action (for task_type=action)"},
                    "trigger_type": {"type": "string", "enum": ["schedule", "event"],
                                     "description": "schedule = time-based, event = count-based"},
                    "schedule": {"type": "string", "enum": ["once", "daily", "weekly", "monthly", "cron"],
                                 "description": "Schedule frequency (for trigger_type=schedule). Use cron for compact weekday ranges such as Mo-Fr / weekdays."},
                    "scheduled_time": {"type": "string", "description": "HH:MM in the user's local clock time (for schedule triggers). The backend resolves the owner/default-assistant timezone and stores next_run as UTC."},
                    "scheduled_day": {"type": "integer", "description": "Day of week 0=Mon (weekly) or day of month (monthly)"},
                    "cron_expression": {"type": "string", "description": "Five-field cron expression for schedule='cron'. Use one task with cron like '0 9 * * 1-5' for weekdays at 09:00 instead of creating one task per weekday."},
                    "trigger_event": {"type": "string", "enum": ["session_created", "message_sent", "document_created", "memory_added", "research_completed", "email_received", "skill_added"],
                                      "description": "Event name (for trigger_type=event)"},
                    "trigger_count": {"type": "integer", "description": "Fire every N events (for trigger_type=event)"},
                    "output_target": {"type": "string", "description": "Where results go. Defaults to 'session' (results land in a dedicated chat session the user reads) — this is the right choice for 'summarize for me' / 'send to me'. Use 'telegram' only for the server-side safe notification boundary; do not pass chat IDs or tokens. Do NOT go hunting for the user's email address; only use an email MCP tool name here if the user explicitly asked to be emailed AND an address is already known."},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_calendar",
            "description": "Manage calendar events: list events in a date range, create, update, delete. delete_event requires confirmed=true after explicit user confirmation. Each event can carry a tag/category (event_type) and importance level. Resolve relative dates like today/tomorrow against the 'Current date and time' system context, then pass ISO 8601 datetimes in the user's local wall time; for all-day events set all_day=true and pass YYYY-MM-DD. For event reminders/alarms, pass reminder_minutes; the tool creates the Odysseus note reminder, so do not also call manage_notes for the same reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list_events", "create_event", "update_event", "delete_event", "list_calendars"],
                               "description": "Action to perform"},
                    "summary": {"type": "string", "description": "Event title (for create/update)"},
                    "dtstart": {"type": "string", "description": "Start ISO datetime, or YYYY-MM-DD if all_day"},
                    "dtend": {"type": "string", "description": "End ISO datetime; defaults to +1h (or +1 day for all_day)"},
                    "all_day": {"type": "boolean", "description": "Whether this is an all-day event"},
                    "description": {"type": "string", "description": "Event description / notes"},
                    "location": {"type": "string", "description": "Event location"},
                    "uid": {"type": "string", "description": "Event UID (for update/delete)"},
                    "calendar_href": {"type": "string", "description": "Specific calendar URL (optional; defaults to first calendar)"},
                    "calendar": {"type": "string", "description": "Filter list_events by calendar name or href"},
                    "start": {"type": "string", "description": "list_events range start (ISO datetime); defaults to today. Prefer start; backend also accepts start_date, range_start, from, dtstart, since."},
                    "end": {"type": "string", "description": "list_events range end (ISO datetime); defaults to +14 days. Prefer end; backend also accepts end_date, range_end, to, dtend, until."},
                    "event_type": {"type": "string", "description": "Tag / category for the event. Common values: work, personal, health, travel, meal, social, admin, other. Aliases accepted: tag, category, type."},
                    "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"], "description": "Priority level (defaults to 'normal')"},
                    "reminder_minutes": {"type": "integer", "description": "For create_event: create an Odysseus reminder this many minutes before the event, e.g. 5 for 'reminder 5 min before'."},
                    "rrule": {"type": "string", "description": "Recurrence rule in iCalendar RRULE format, e.g. 'FREQ=WEEKLY;BYDAY=MO' for weekly on Monday. Use with create_event or update_event."},
                    "confirmed": {"type": "boolean", "description": "Required true for delete_event after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_notes",
            "description": "Manage notes and checklists (Google Keep-style): list, add, update, delete, toggle_item. Delete requires confirmed=true after explicit user confirmation. IMPORTANT: For to-do lists / checklists, set note_type='checklist' and pass the items as the `checklist_items` array — do NOT serialize them into `content` as plain text. For freeform notes, use note_type='note' and put the body in `content`. `due_date` accepts natural language like 'tomorrow at 9am' (parsed in the user's timezone) and fires a notification — do not also create a calendar event for the same reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "add", "update", "delete", "toggle_item"],
                               "description": "The action to perform"},
                    "id": {"type": "string", "description": "Note id (for update/delete/toggle_item); 8-char prefix is fine"},
                    "title": {"type": "string", "description": "Note title (for add/update)"},
                    "content": {"type": "string", "description": "Freeform body text. Use this for note_type='note'. Do NOT use this for checklists — pass `checklist_items` instead."},
                    "note_type": {"type": "string", "enum": ["note", "checklist"],
                                  "description": "'note' = freeform text in `content`. 'checklist' = structured to-do items in `checklist_items`. Defaults to 'checklist' if checklist_items is supplied, else 'note'."},
                    "checklist_items": {"type": "array",
                                        "items": {"type": "object",
                                                  "properties": {
                                                      "text": {"type": "string", "description": "The to-do item text"},
                                                      "done": {"type": "boolean", "description": "Whether the item is checked off"}
                                                  },
                                                  "required": ["text"]},
                                        "description": "Checklist items for note_type='checklist'. Each item is {text, done}. REQUIRED for checklists — leaving this empty produces a blank note."},
                    "color": {"type": "string", "description": "Optional color label (e.g. 'yellow', 'blue', 'green')"},
                    "label": {"type": "string", "description": "Optional category label (also used as a list filter)"},
                    "pinned": {"type": "boolean", "description": "Pin the note to the top"},
                    "archived": {"type": "boolean", "description": "For update: archive/unarchive. For list: show archived notes when true."},
                    "due_date": {"type": "string", "description": "Reminder time. Accepts natural language ('tomorrow at 9am', '11pm today') or ISO 8601. Fires a notification at that time."},
                    "index": {"type": "integer", "description": "Checklist item index (for toggle_item, 0-based)"},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "api_call",
            "description": "Call a registered API integration (RSS reader, git forge, bookmark manager, smart home, etc.). Check the system context for available integrations and their endpoints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "integration": {"type": "string", "description": "Integration name or ID (e.g. 'Miniflux', 'Gitea')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method"},
                    "path": {"type": "string", "description": "API endpoint path (e.g. '/v1/entries?status=unread&limit=20')"},
                    "body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)"}
                },
                "required": ["integration", "method", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_teacher",
            "description": "Ask a more capable AI model for help when stuck on a difficult problem. The teacher provides guidance that can be saved as a learned skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Teacher model name (e.g. 'claude-sonnet-4') or 'auto' for configured default"},
                    "problem": {"type": "string", "description": "Describe the problem or question you need help with"}
                },
                "required": ["problem"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skills",
            "description": (
                "Read or modify the user's skill library. Skills are SKILL.md files "
                "(YAML frontmatter + structured body: When to Use / Procedure / "
                "Pitfalls / Verification) and follow a draft → published lifecycle. "
                "Use progressive disclosure: 'list' to see what exists, 'view' to "
                "load full content for a single skill, 'view_ref' for sub-files. "
                "Use 'patch' for surgical text edits and 'edit' for full rewrites. "
                "'publish' once you've verified the procedure works. Delete requires "
                "confirmed=true after explicit user confirmation. For add, "
                "always provide an explicit name slug and only tell the user the "
                "exact name returned by the tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "view_ref", "add", "edit", "patch", "publish", "delete", "search"], "description": "list = name+description summary; view = full SKILL.md; view_ref = sub-file under the skill dir; add = create; edit = full rewrite (content); patch = old_string→new_string; publish = flip status; delete; search = relevance match on published skills."},
                    "name": {"type": "string", "description": "Slug/name of the skill. Required for add/view/view_ref/edit/patch/publish/delete. For add, choose the exact kebab-case name the user should see and report only the returned name."},
                    "path": {"type": "string", "description": "Sub-path under the skill directory for view_ref (e.g. 'references/example.md')."},
                    "description": {"type": "string", "description": "One-line summary surfaced in the skills index (for add)."},
                    "category": {"type": "string", "description": "Organizational grouping like 'dev', 'email', 'system' (for add)."},
                    "when_to_use": {"type": "string", "description": "Trigger conditions in plain English (for add)."},
                    "procedure": {"type": "array", "items": {"type": "string"}, "description": "Numbered steps (for add)."},
                    "pitfalls": {"type": "array", "items": {"type": "string"}, "description": "Known failure modes + recovery (for add)."},
                    "verification": {"type": "array", "items": {"type": "string"}, "description": "How to confirm the procedure succeeded (for add)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Keyword tags (for add)."},
                    "platforms": {"type": "array", "items": {"type": "string"}, "description": "Restrict to OSes (for add)."},
                    "requires_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide unless these toolsets are active (for add)."},
                    "fallback_for_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide when these toolsets are active (for add)."},
                    "status": {"type": "string", "enum": ["draft", "published"], "description": "Defaults to 'draft' on add."},
                    "version": {"type": "string", "description": "Semver-ish, e.g. '1.0.0' (for add)."},
                    "confidence": {"type": "number", "description": "0-1 (for add/publish)."},
                    "content": {"type": "string", "description": "Full SKILL.md text (for edit)."},
                    "old_string": {"type": "string", "description": "Exact substring to replace (for patch). Must appear exactly once."},
                    "new_string": {"type": "string", "description": "Replacement text (for patch)."},
                    "query": {"type": "string", "description": "Search query (for search)."},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_endpoints",
            "description": "Manage model API endpoints through the same admin routes as the UI: list, add, update, delete, enable, or disable. Mutating actions require confirmed=true after explicit user confirmation. Do not pass raw API keys through chat; provider credentials require secure UI handoff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "update", "delete", "enable", "disable"]},
                    "endpoint_id": {"type": "string", "description": "Endpoint ID (for update/delete/enable/disable)"},
                    "name": {"type": "string", "description": "Display name (for add)"},
                    "base_url": {"type": "string", "description": "API base URL e.g. https://api.openai.com/v1 (for add)"},
                    "api_key": {"type": "string", "description": "Deprecated for agent use: raw API keys are blocked and must be entered through secure UI handoff."},
                    "skip_probe": {"type": "boolean", "description": "Skip initial model-list probe when adding an endpoint."},
                    "require_models": {"type": "boolean", "description": "Require model discovery to return at least one model when adding."},
                    "model_type": {"type": "string", "description": "Endpoint model type, e.g. llm or image."},
                    "endpoint_kind": {"type": "string", "description": "Endpoint kind, e.g. auto, api, proxy, ollama."},
                    "model_refresh_mode": {"type": "string", "description": "Model cache refresh mode."},
                    "model_refresh_interval": {"type": "integer", "description": "Model cache refresh interval in seconds."},
                    "model_refresh_timeout": {"type": "integer", "description": "Model cache refresh timeout in seconds."},
                    "supports_tools": {"type": "boolean", "description": "Whether the endpoint supports tool calling."},
                    "pinned_models": {"description": "Pinned model IDs as a list, JSON string, comma/newline string, or route-compatible value."},
                    "container_local": {"type": "boolean", "description": "Treat loopback URL as local to the Odysseus container when adding."},
                    "shared": {"type": "boolean", "description": "Whether a new endpoint is shared globally; false scopes it to the current admin owner."},
                    "confirmed": {"type": "boolean", "description": "Required true for add/update/delete/enable/disable after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_mcp",
            "description": "Manage MCP (Model Context Protocol) tool servers. list/list_tools are read-only; add/delete/enable/disable/reconnect require confirmed=true. Agent-added stdio servers are additionally restricted to the MCP command allowlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable", "reconnect", "list_tools"]},
                    "server_id": {"type": "string", "description": "Server ID (for delete/enable/disable/reconnect)"},
                    "name": {"type": "string", "description": "Server name (for add)"},
                    "command": {"type": "string", "description": "Command to run e.g. npx (for add)"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments (for add)"},
                    "env": {"type": "object", "description": "Environment variables (for add)"},
                    "confirmed": {"type": "boolean", "description": "Required true for add/delete/enable/disable/reconnect after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_webhooks",
            "description": "Manage webhooks through admin routes. Webhook URLs are masked in tool output; add/test/delete/enable/disable require confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "test", "delete", "enable", "disable"]},
                    "webhook_id": {"type": "string", "description": "Webhook ID (for test/delete/enable/disable)"},
                    "name": {"type": "string", "description": "Webhook name (for add)"},
                    "url": {"type": "string", "description": "Webhook URL (for add)"},
                    "events": {"type": "string", "description": "Comma-separated event names (for add)"},
                    "confirmed": {"type": "boolean", "description": "Required true for add/test/delete/enable/disable after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tokens",
            "description": "Manage API access tokens through the same admin routes as the UI/API. create/update/delete require confirmed=true. Newly created token values are shown once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "update", "rename", "delete"]},
                    "token_id": {"type": "string", "description": "Token ID (for update/rename/delete)"},
                    "name": {"type": "string", "description": "Token name (for create/update/rename)"},
                    "scopes": {"description": "Comma-separated string or list of scopes (for create/update)"},
                    "profile": {"type": "string", "description": "Optional token profile (for create)"},
                    "confirmed": {"type": "boolean", "description": "Required true for create/update/delete after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_presets",
            "description": "Manage chat/persona presets through the same routes as the Presets UI. list/templates/groups are read-only; update_custom/save_template/delete_template/save_groups require confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "templates", "groups", "update_custom", "save_template", "delete_template", "save_groups"]},
                    "template_id": {"type": "string", "description": "Template ID (for save_template update or delete_template)"},
                    "id": {"type": "string", "description": "Alias for template_id"},
                    "name": {"type": "string", "description": "Preset/template display name"},
                    "enabled": {"type": "boolean", "description": "Whether the custom preset is enabled"},
                    "temperature": {"type": "number", "description": "Preset temperature, 0.0-2.0"},
                    "max_tokens": {"type": "integer", "description": "Maximum tokens; 0 means no preset limit"},
                    "system_prompt": {"type": "string", "description": "System prompt text for custom preset or template"},
                    "inject_prefix": {"type": "string", "description": "Text prepended to outgoing user messages for custom preset"},
                    "inject_suffix": {"type": "string", "description": "Text appended to outgoing user messages for custom preset"},
                    "groups": {"type": "array", "items": {"type": "object"}, "description": "Full group preset list for save_groups"},
                    "confirmed": {"type": "boolean", "description": "Required true for update_custom/save_template/delete_template/save_groups after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_personal_docs",
            "description": "Manage Personal Docs / RAG source directories through the same routes as the Personal Docs UI. list is read-only; reload/add_directory/remove_directory/delete_file require confirmed=true. Upload stays UI-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "reload", "add_directory", "remove_directory", "delete_file", "upload"]},
                    "directory": {"type": "string", "description": "Directory path under the configured personal documents root (for add_directory/remove_directory)"},
                    "path": {"type": "string", "description": "Alias for directory or filepath"},
                    "filepath": {"type": "string", "description": "File path to delete/exclude from Personal Docs/RAG"},
                    "file_path": {"type": "string", "description": "Alias for filepath"},
                    "confirmed": {"type": "boolean", "description": "Required true for reload/add_directory/remove_directory/delete_file after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_embeddings",
            "description": "Manage local embedding models and embedding endpoint status through the same routes as the Embedding Settings UI. list/status/endpoint are read-only; download/delete/clear_endpoint require confirmed=true. set_endpoint stays UI/secure-handoff-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "status", "endpoint", "download", "delete", "clear_endpoint", "set_endpoint"]},
                    "model_name": {"type": "string", "description": "Embedding model name, e.g. BAAI/bge-small-en-v1.5"},
                    "model": {"type": "string", "description": "Alias for model_name"},
                    "confirmed": {"type": "boolean", "description": "Required true for download/delete/clear_endpoint after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_assistant",
            "description": "Manage the per-user personal assistant through the same routes as the Assistant UI. session/settings/timezones/run_status are read-only; update/run require confirmed=true. endpoint_url stays UI/manage_endpoints-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["session", "settings", "timezones", "run_status", "update", "run"]},
                    "task_id": {"type": "string", "description": "Assistant check-in task id (for run/run_status)"},
                    "id": {"type": "string", "description": "Alias for task_id"},
                    "name": {"type": "string", "description": "Assistant display name (for update)"},
                    "avatar": {"type": "string", "description": "Assistant avatar glyph or URL (for update)"},
                    "personality": {"type": "string", "description": "Assistant personality prompt (for update)"},
                    "model": {"type": "string", "description": "Assistant model id (for update)"},
                    "enabled_tools": {"type": "array", "items": {"type": "string"}, "description": "Full enabled tool list (for update)"},
                    "allow_autonomous_email": {"type": "boolean", "description": "Convenience toggle for send_email/reply_to_email tools (for update)"},
                    "timezone": {"type": "string", "description": "IANA timezone such as Europe/Berlin (for update)"},
                    "check_ins": {"type": "array", "items": {"type": "object"}, "description": "Check-in updates with id/name/scheduled_time/prompt/enabled"},
                    "endpoint_url": {"type": "string", "description": "Blocked in agent tool; use UI/manage_endpoints-only."},
                    "confirmed": {"type": "boolean", "description": "Required true for update/run after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_plugins",
            "description": "Manage admin plugin manager routes through the same Plugins UI routes. list/registry/registries/status are read-only; enable/disable/reload/rescan/install/uninstall/add_registry/remove_registry require confirmed=true. install accepts registry plugin ids only; direct ZIP URL installs and plugin-specific provider actions stay UI/provider-specific.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "registry", "registries", "status", "enable", "disable", "reload", "rescan", "install", "uninstall", "add_registry", "remove_registry"]},
                    "plugin_id": {"type": "string", "description": "Plugin id for status/enable/disable/reload/install/uninstall"},
                    "id": {"type": "string", "description": "Alias for plugin_id"},
                    "url": {"type": "string", "description": "Registry URL for add_registry/remove_registry. Direct install URL is blocked."},
                    "registry_url": {"type": "string", "description": "Alias for url when adding/removing plugin registries"},
                    "confirmed": {"type": "boolean", "description": "Required true for enable/disable/reload/rescan/install/uninstall/add_registry/remove_registry after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_documents",
            "description": "Manage documents: list all documents (with optional search/language filter), delete documents, or run tidy cleanup. delete/tidy require confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "delete", "tidy"]},
                    "document_id": {"type": "string", "description": "Document ID (for delete)"},
                    "search": {"type": "string", "description": "Search query (for list)"},
                    "language": {"type": "string", "description": "Filter by language (for list)"},
                    "limit": {"type": "integer", "description": "Max results (for list, default 50)"},
                    "confirmed": {"type": "boolean", "description": "Required true for delete/tidy after explicit user confirmation."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_settings",
            "description": "Manage registered settings and feature flags through the policy-aware settings service. Use list/get/set/patch/reset/explain for settings, features for feature flags, and disable_tool/enable_tool/list_tools for legacy tool toggles. Secret settings require secure handoff; confirm-protected settings need confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "set", "patch", "delete", "reset", "explain", "features", "request_secret", "secret_handoffs", "disable_tool", "enable_tool", "list_tools"]},
                    "key": {"type": "string", "description": "Setting or feature key (for get/set/patch/reset/explain/features/request_secret). Common aliases like 'default model' and 'token budget' are accepted for settings."},
                    "scope": {"type": "string", "enum": ["auto", "user", "global"], "description": "Where to read/write a setting. auto prefers the current user when the setting supports user overrides."},
                    "store": {"type": "string", "enum": ["setting", "feature"], "description": "Use feature for feature flags; otherwise setting is assumed."},
                    "patch": {"type": "object", "description": "Structured patch object for list/object settings, e.g. {'op':'append','value':'brave'} or {'op':'set','path':'search','value':'ctrl+j'}."},
                    "op": {"type": "string", "enum": ["append", "remove", "replace", "clear", "set"], "description": "Patch operation when patch is not supplied."},
                    "path": {"type": "string", "description": "Object key/path for patch operations."},
                    "patch_key": {"type": "string", "description": "Alternative object key/path for patch operations."},
                    "confirmed": {"type": "boolean", "description": "Set true only after explicit user confirmation for confirm-protected settings or feature flags."},
                    "ttl_seconds": {"type": "integer", "description": "Optional expiration window for request_secret handoffs; capped server-side."},
                    "status": {"type": "string", "enum": ["pending", "completed", "cancelled", "expired"], "description": "Filter for secret_handoffs."},
                    "value": {"description": "Setting value (for set/features) or patch value (for patch); can be string, number, boolean, list, or object"},
                    "tool": {"type": "string", "description": "Tool name to disable/enable (for disable_tool/enable_tool). Accepts aliases: shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email — or a raw tool name like 'bash' or 'web_search'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recent_changes",
            "description": "Read or create local Odysseus patch-note snapshots. Use when the user asks what changed, what is new, Neuerungen, Patch Notes, or changes in the last N hours. This checks local commits, dirty files, untracked files, and recently modified files; it does not search the web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["collect", "list", "read"], "description": "collect creates or returns a recent snapshot; list shows stored snapshots; read returns one stored snapshot."},
                    "hours": {"type": "integer", "description": "Lookback window for collect, default 12."},
                    "limit": {"type": "integer", "description": "Max history rows for list, default 20."},
                    "snapshot_id": {"type": "string", "description": "Snapshot id for read, or latest."},
                    "persist": {"type": "boolean", "description": "Whether collect should store the snapshot in history, default true."},
                    "force": {"type": "boolean", "description": "Store even if the fingerprint matches the latest snapshot."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_repos",
            "description": "Manage explicitly registered repositories, read Git facts, collect repo-scoped change intelligence, and preview commit/push/forge plans. This tool never commits or delivers to a provider; use commit_project once for the sole commit-and-policy-sync workflow. Read actions list/get/status/log/diff_stat/changed_paths/remotes/changes/change_history/commit_plan/push_plan/forge_plan need no confirmation. Registry mutations register/forget/update_policy require confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "status", "log", "diff_stat", "changed_paths", "remotes", "changes", "change_history", "commit_plan", "push_plan", "forge_plan", "register", "forget", "update_policy"], "description": "Read repository facts, preview commit/push/forge policy plans, or maintain the registry. Execution of commits and provider delivery belongs only to commit_project."},
                    "repo_id": {"type": "string", "description": "Repo id for get/status/log/diff_stat/changed_paths/remotes/changes/change_history/commit_plan/push_plan/forge_plan/forget/update_policy; optional for register when title/path can produce one."},
                    "id": {"type": "string", "description": "Alias for repo_id."},
                    "title": {"type": "string", "description": "Human title for register."},
                    "owner": {"type": "string", "description": "Owner label for register."},
                    "repo_kind": {"type": "string", "enum": ["odysseus", "project", "user", "external"], "description": "Repo kind for register."},
                    "path_ref": {"type": "string", "description": "Registry path reference, relative to the repo workspace base; no host-local absolute paths."},
                    "workspace_root": {"type": "string", "description": "Relative workspace root for register, e.g. projects/my-app."},
                    "project_root": {"type": "string", "description": "Relative repo root for register, e.g. projects/my-app/repo or repos/demo."},
                    "system_root": {"type": "string", "description": "Optional relative system root; no host-local absolute paths."},
                    "default_branch": {"type": "string", "description": "Default branch for register."},
                    "current_branch": {"type": "string", "description": "Optional current branch marker for register."},
                    "remotes": {"type": "array", "items": {"type": "object"}, "description": "Remote policy objects with name, url or url_redacted, purpose, push_policy."},
                    "privacy_class": {"type": "string", "enum": ["public", "private", "sensitive"], "description": "Privacy class for register/update_policy. Private/sensitive default local-only."},
                    "provider_scope": {"type": "string", "enum": ["default", "local_only", "external_allowed"], "description": "Provider scope for register/update_policy."},
                    "allowed_actions": {"type": "array", "items": {"type": "string"}, "description": "Per-repo allowed action list for register/update_policy."},
                    "linked_project_slug": {"type": "string", "description": "Optional Project Runner slug for register."},
                    "operator_go": {"type": "boolean", "description": "Required true, in addition to confirmed=true, when registering outside allowed registry roots, executing a live push, or fetching live forge metadata."},
                    "confirmed": {"type": "boolean", "description": "Required true for registry mutations register, forget, and update_policy."},
                    "changed_paths": {"type": "array", "items": {"type": "string"}, "description": "Exact repo-relative file paths reviewed for commit_plan/commit. Directories, absolute paths, .git, .env, and key files are blocked."},
                    "paths": {"type": "array", "items": {"type": "string"}, "description": "Alias for changed_paths."},
                    "objective": {"type": "string", "description": "Short objective used for commit planning and default commit message."},
                    "summary": {"type": "string", "description": "Alias for objective."},
                    "commit_message": {"type": "string", "description": "Optional safe commit message for commit_plan/commit; no secrets or multiline payloads."},
                    "checks_passed": {"type": "boolean", "description": "Required true for commit to confirm focused tests or quality gates passed."},
                    "content_reviewed": {"type": "boolean", "description": "Required true for commit to confirm no secret, private-content, or DSGVO risk is included."},
                    "remote_name": {"type": "string", "description": "Remote name for push_plan/push, e.g. fuzzy. The registry remote policy must allow push."},
                    "remote": {"type": "string", "description": "Alias for remote_name."},
                    "provider": {"type": "string", "enum": ["github", "gitea", "forgejo"], "description": "Forge provider considered by forge_plan only."},
                    "remote_provider": {"type": "string", "enum": ["github", "gitea", "forgejo"], "description": "Alias for provider."},
                    "namespace": {"type": "string", "description": "Provider namespace/org/user considered by forge_plan."},
                    "remote_namespace": {"type": "string", "description": "Alias for namespace."},
                    "repo_name": {"type": "string", "description": "Provider repo name considered by forge_plan; defaults to the registered repo path tail."},
                    "api_base_url": {"type": "string", "description": "Optional API base URL for self-hosted Gitea/Forgejo. Tokens or credentials must never be included."},
                    "integration_id": {"type": "string", "description": "Optional server-side integration id for existing forge credentials."},
                    "auth_ready": {"type": "boolean", "description": "Readiness input for forge_plan; it never triggers provider access."},
                    "create_repo_requested": {"type": "boolean", "description": "Planning input only; manage_repos never creates a provider repository."},
                    "branch_name": {"type": "string", "description": "Branch name for push_plan/push. Must match the current branch and pass remote policy."},
                    "branch": {"type": "string", "description": "Alias for branch_name."},
                    "commit_sha": {"type": "string", "description": "Expected current HEAD SHA for push_plan/push; must match the local repo HEAD."},
                    "commit_ref": {"type": "string", "description": "Alias for commit_sha."},
                    "live_enabled": {"type": "boolean", "description": "Required true for live push unless ODYSSEUS_REPO_PUSH_RUNNER_LIVE_ENABLED is enabled server-side."},
                    "hours": {"type": "integer", "description": "Lookback window for changes, default 12."},
                    "persist": {"type": "boolean", "description": "Whether changes stores the sanitized repo-scoped snapshot, default true."},
                    "force": {"type": "boolean", "description": "When true, changes stores a snapshot even if the fingerprint matches the latest stored capsule."},
                    "limit": {"type": "integer", "description": "Commit count for log or stored capsule count for change_history, default 10 for log and 20 for change_history."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "commit_project",
            "description": "Create one reviewed local Git commit with a title and description, retain it in the owner-scoped local Forge, and queue the providers selected by the stored project policy. This is the only project commit/provider action. Do not choose a provider in this call.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "repo_id": {"type": "string", "description": "Registered project repository id"},
                    "title": {"type": "string", "description": "Git commit title"},
                    "description": {"type": "string", "description": "Human-readable Git commit description/body"},
                    "version_label": {"type": "string", "description": "Optional human version label"},
                    "change_notes": {"type": "array", "items": {"type": "string"}, "description": "Optional reviewed change notes"},
                    "reviewed_paths": {"type": "array", "minItems": 1, "items": {"type": "string"}, "description": "Exact repo-relative paths reviewed for this commit"},
                    "checks_passed": {"type": "boolean", "description": "True only after required checks passed"},
                    "content_reviewed": {"type": "boolean", "description": "True only after the staged content was reviewed"},
                    "confirmed": {"type": "boolean", "description": "Explicit confirmation for this effectful commit"},
                    "idempotency_key": {"type": "string", "description": "Stable unique key for safe replay of this logical commit request"}
                },
                "required": ["repo_id", "title", "description", "reviewed_paths", "checks_passed", "content_reviewed", "confirmed", "idempotency_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "publish_artifact",
            "description": "Publish a generated .py, self-contained .html, or real .png workspace file as an owner-scoped chat attachment. Use after creating the deliverable; set inspect_image=true for screenshots that must be visually verified. This returns the only valid download attachment metadata. It never makes a native GUI interactive in the browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path of the generated file"},
                    "name": {"type": "string", "description": "Optional safe download filename; must keep the extension"},
                    "inspect_image": {"type": "boolean", "description": "Run owner-scoped vision analysis for a PNG before claiming visual inspection"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_pygame_headless",
            "description": "Run bounded Pygame verification with SDL dummy video/audio drivers. Checks Python syntax, the installed pygame import, a capped frame run, and a real PNG capture. Success proves headless_tested only; it never proves interactive_preview_ready or visual_inspected. Publish the .py afterward, then publish the PNG with inspect_image=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative .py file"},
                    "screenshot_path": {"type": "string", "description": "Optional safe workspace-relative .png output path"},
                    "max_frames": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 120},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
                    "capture_frame": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 1}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_github_issues",
            "description": "GitHub Issue Intelligence. Use for local duplicate issue preview, bounded provider sync, triaged issue creation planning, and GitHub Issue Fields/label fallback planning. duplicate_search is local/read-only over already-synced issue records. sync is read-only against GitHub and writes only local IssueRecord rows when confirmed=true and server-side env gates plus repository allowlist are enabled; it never accepts provider tokens in chat. create_triaged and set_fields are write-like and require confirmed=true plus future live/auth gates; without those gates they return a safe plan/blocker instead of writing to GitHub.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["sync", "duplicate_search", "create_triaged", "set_fields"], "description": "sync performs bounded read-only provider sync only when confirmed and server-side gates are enabled; duplicate_search returns local top duplicate candidates; create_triaged plans a confirmed issue create; set_fields plans GitHub Issue Fields or label fallback updates."},
                    "repository": {"type": "string", "description": "Provider repository slug, e.g. fuzzy123-ai/odysseus-fuzzy."},
                    "title": {"type": "string", "description": "Draft issue title for duplicate_search or create_triaged."},
                    "body": {"type": "string", "description": "Draft issue body. The duplicate report returns body length, not raw private provider content."},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "Draft or desired issue labels."},
                    "external_id": {"type": "string", "description": "Existing provider issue number/id; excluded from duplicate_search when present."},
                    "issue_ref": {"type": "string", "description": "Target issue ref for set_fields, e.g. #123 or provider issue id."},
                    "source_issue_id": {"type": "string", "description": "Local GitHubIssueRecord id when persisting duplicate evidence."},
                    "persist": {"type": "boolean", "description": "For duplicate_search, persist pending local duplicate evidence when source_issue_id is provided."},
                    "record": {"type": "boolean", "description": "Alias for persist."},
                    "top_k": {"type": "integer", "description": "Max duplicate candidates, default 3."},
                    "max_items": {"type": "integer", "description": "For sync, maximum provider issues to read in one bounded run, capped server-side."},
                    "limit": {"type": "integer", "description": "Alias for max_items on sync."},
                    "include_closed": {"type": "boolean", "description": "Whether duplicate_search includes closed issues, default true."},
                    "fields": {"type": "object", "description": "Canonical issue fields: type, priority, effort, area, status, start_date, target_date, duplicate_of."},
                    "type": {"type": "string", "description": "Canonical issue type field."},
                    "priority": {"type": "string", "description": "Canonical issue priority field."},
                    "effort": {"type": "string", "description": "Canonical issue effort field."},
                    "area": {"type": "string", "description": "Canonical area field."},
                    "status": {"type": "string", "description": "Canonical status field."},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD start date."},
                    "target_date": {"type": "string", "description": "YYYY-MM-DD target date."},
                    "duplicate_of": {"type": "string", "description": "Duplicate reference such as #123."},
                    "duplicate_confirmed": {"type": "boolean", "description": "For create_triaged, acknowledges high-confidence duplicate candidates."},
                    "confirmed": {"type": "boolean", "description": "Required true for live-read sync and for write-like create_triaged/set_fields after explicit user confirmation."},
                    "operator_go": {"type": "boolean", "description": "Future live write gate; this repo-only slice only reports the gate."},
                    "live_enabled": {"type": "boolean", "description": "Future live write gate; this repo-only slice only reports the gate."},
                    "auth_ready": {"type": "boolean", "description": "Future provider credential gate; never pass tokens in chat."}
                },
                "required": ["action", "repository"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_nextcloud_transfer",
            "description": "Universal Inbox Nextcloud transfer tool. Use for Nextcloud WebDAV readiness, safe smoke planning, and copy-only Universal Inbox writes. readiness checks server-side env gates without exposing secrets; smoke_plan prepares a harmless test transfer without writing; execute writes only after review_approved/confirmed=true, operator_live_go=true, live env gates, and server-side WebDAV config. Never accept Nextcloud credentials in chat; deletes, moves and overwrites stay blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["readiness", "smoke_plan", "execute"], "description": "readiness checks server-side gates; smoke_plan prepares a no-write test transfer; execute performs dry-run or live copy-only transfer under explicit gates."},
                    "target_path": {"type": "string", "description": "Relative Nextcloud target path. Must not be absolute and must not contain traversal."},
                    "sidecar_path": {"type": "string", "description": "Relative Nextcloud metadata sidecar path. Must not be absolute and must not contain traversal."},
                    "source_path": {"type": "string", "description": "Optional local reviewed source artifact path for execute. Omit for the built-in harmless smoke text."},
                    "smoke_text": {"type": "string", "description": "Optional harmless smoke text used when source_path is omitted."},
                    "dry_run": {"type": "boolean", "description": "Defaults true. When true, never builds a WebDAV client and never writes."},
                    "review_approved": {"type": "boolean", "description": "Required true for execute to write; means human/document review approved this transfer."},
                    "confirmed": {"type": "boolean", "description": "Alias for review_approved."},
                    "operator_live_go": {"type": "boolean", "description": "Required true for execute with dry_run=false."},
                    "live_go": {"type": "boolean", "description": "Alias for operator_live_go."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_model",
            "description": "Download a HuggingFace model to a server. If `host` is omitted, defaults to the cookbook's currently-selected server (NOT localhost) — call list_cookbook_servers first if you're unsure where it should go.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "HuggingFace repo (e.g. 'Qwen/Qwen3-8B')"},
                    "host": {"type": "string", "description": "Target server — use the friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or a raw user@host. Omit to use the cookbook's selected default server."},
                    "local": {"type": "boolean", "description": "Force download to THIS machine (localhost) instead of the default remote server."},
                    "include": {"type": "string", "description": "Glob filter for specific files (e.g. '*Q4_K_M*')"},
                },
                "required": ["repo_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_model",
            "description": "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, or Diffusers. If `host` is omitted, defaults to the cookbook's selected server (not localhost). For image/inpainting/diffusion models use the built-in command `python3 scripts/diffusion_server.py --model <repo> --port 8100` rather than inventing a custom diffusers API server. After launching, call list_served_models to check readiness/errors; if it reports a diagnosis with retry suggestions, retry via serve_model using the suggested adjusted cmd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Model repo (e.g. 'Qwen/Qwen3-8B')"},
                    "cmd": {"type": "string", "description": "Full serve command (e.g. 'vllm serve Qwen/Qwen3-8B --port 8000 --tp 2', 'python3 -m sglang.launch_server --model-path Qwen/Qwen3-8B --port 30000', or for inpainting/image models: 'python3 scripts/diffusion_server.py --model diffusers/stable-diffusion-xl-1.0-inpainting-0.1 --port 8100')"},
                    "host": {"type": "string", "description": "Target server — friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or raw user@host. Omit to use the cookbook's selected default."},
                    "local": {"type": "boolean", "description": "Force serve on THIS machine instead of the default remote server."},
                },
                "required": ["repo_id", "cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_served_models",
            "description": "List currently running model servers with status, model name, port, throughput, and structured Cookbook diagnoses. If a serve failed, this includes recent logs plus retry suggestions/adjusted commands the agent can use with serve_model.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_served_model",
            "description": "Stop a running model server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID of the server to stop"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tail_serve_output",
            "description": "Read the last N lines of a cookbook serve/download task's tmux pane. Use ONLY in this exact sequence: (1) the user asked to serve a model, (2) you launched it via serve_model, (3) list_served_models reports the NEW task as crashed/error, (4) call tail_serve_output on the new sessionId to find the root cause, (5) call serve_model again with adjusted flags. DO NOT call this on old stopped/completed download tasks — they are historical and won't tell you anything about the current attempt. DO NOT investigate past failures before launching; the environment may have changed since.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session id from list_served_models (e.g. 'serve-abc12345', 'cookbook-a1b2c3d4')."},
                    "tail": {"type": "integer", "description": "How many lines of pane scrollback to fetch (default 300, max 4000). Bump this if the error in the visible tail references an earlier line ('see root cause above')."},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_downloads",
            "description": "List in-progress model downloads in the Cookbook. Shows each download's model name, phase, percent (if available), session ID, and remote host.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_download",
            "description": "Cancel an in-progress model download by killing its tmux session. Use list_downloads first to get the session_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID from list_downloads (e.g. 'cookbook-a1b2c3d4')"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hf_models",
            "description": "Search HuggingFace for models matching a query. Returns a ranked list of repo IDs, sizes (when available), and download counts. Use this when the user wants to find a model to download.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (e.g. 'Qwen 8B', 'flux', 'llama-3 instruct')"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cookbook_servers",
            "description": "List the cookbook's configured servers (remote GPU boxes + local) and the current default host. Call this before download_model/serve_model when the user didn't specify a host, so models go to the right machine (where the GPUs and model cache are) instead of localhost. If multiple servers and intent is ambiguous, show them and ask the user which.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_serve_presets",
            "description": "List saved Cookbook serve presets. Each preset is a launch template (name, model, host, port, tmux cmd) the user previously saved from the UI. Call this BEFORE serve_model when the user asks to launch a model by name — there's almost always a working preset for it.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "adopt_served_model",
            "description": "Register an existing tmux model server (started manually or outside the cookbook flow) into Cookbook tracking, AND add it as a chat endpoint. Use when the user (or you) launched something via ssh+tmux and now want it visible in the UI / stoppable via stop_served_model / usable in the model picker. Verifies the tmux session + port respond before adding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Remote host in user@host form (e.g. 'user@192.0.2.10'). Omit for localhost."},
                    "tmux_session": {"type": "string", "description": "Existing tmux session name (e.g. 'minimax-m27')"},
                    "model": {"type": "string", "description": "Model repo_id or display name (e.g. 'cyankiwi/MiniMax-M2.7-AWQ-4bit')"},
                    "port": {"type": "integer", "description": "Port the server is listening on (default 8000)"},
                    "name": {"type": "string", "description": "Optional display name (defaults to model basename)"},
                    "add_endpoint": {"type": "boolean", "description": "Also register as a chat endpoint (default true)"}
                },
                "required": ["tmux_session", "model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "serve_preset",
            "description": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user saved before. This is the preferred way to start a known model (SD3.5, vLLM presets, etc.) — don't fabricate launch commands when a preset exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (exact or case-insensitive substring of one returned by list_serve_presets)"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cached_models",
            "description": "List models already cached on disk locally or on a remote server. `host` accepts friendly Cookbook server names from list_cookbook_servers (for example ajax) or raw user@host. Also reports completed Cookbook download tasks when the filesystem cache scan cannot locate the HF cache path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Friendly Cookbook server name (e.g. 'ajax', 'gpu-box') or raw remote host (e.g. 'user@gpu-box'). Omit for local."},
                    "model_dir": {"type": "string", "description": "Comma-separated additional model directories to scan beyond ~/.cache/huggingface/hub"},
                    "ssh_port": {"type": "string", "description": "SSH port for remote host (default 22)"},
                    "platform": {"type": "string", "enum": ["linux", "windows"], "description": "Remote platform"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "app_api",
            "description": "Generic loopback to allowed internal Odysseus endpoints. Use this when there's no named tool for what the user wants. Hits safe UI/API routes for assistant reads, chat status reads, cleanup preview, Codex plugin reads, compare history, contacts reads, cookbook read state, document reads, email reads, embedding status reads, gallery/library reads, memory reads, notes/calendar reads, personal document reads, plugin reads, preset reads, prefs reads, repo reads/status/changes, search config/provider reads, settings reads, research status, skills reads, etc. action='endpoints' returns the OpenAPI surface (use `filter` to narrow). action='call' (default) takes method+path+body. Sensitive auth/user/admin/shell/upload/signature/editor-draft paths, host-control Cookbook mutation routes, direct task/session/chat/rewrite/context-injection/document/research/assistant/codex/compare/contacts/email/embeddings/gallery/memory/notes/calendar/personal/plugins/presets/prefs/skills mutation routes, repo register/policy/commit-plan/push-plan routes, cleanup execution, search execution, and admin mutations are blocked for safety. Assistant, Codex plugin, compare, contacts, documents, email, embeddings, gallery, memory, notes, calendar, personal-docs, plugin-manager/provider, presets, prefs, repos, skills, and chat-run routes are read/list/status only through app_api; upload attachment, saved visual signature, and gallery editor draft routes are blocked entirely. Use named tools such as list_email_accounts, list_emails, read_email, send_email, reply_to_email, bulk_email, archive_email, delete_email, mark_email_read, resolve_contact, manage_contact, manage_memory, manage_notes, manage_calendar, manage_tasks, manage_session, manage_documents, manage_research, manage_settings, manage_skills, manage_endpoints, manage_webhooks, manage_mcp, manage_presets, manage_personal_docs, manage_embeddings, manage_assistant, manage_plugins, manage_repos when available; use ui_control for themes and UI prefs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["call", "endpoints"], "description": "'call' to hit an endpoint, 'endpoints' to list what's available"},
                    "path": {"type": "string", "description": "Endpoint path starting with /api/ (e.g. '/api/cookbook/gpus', '/api/gallery/list', '/api/calendar/events')"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method (default GET)"},
                    "body": {"type": "object", "description": "JSON request body for POST/PUT/PATCH"},
                    "query": {"type": "object", "description": "Querystring params as a key-value object"},
                    "filter": {"type": "string", "description": "For action=endpoints: substring to filter paths/summaries (e.g. 'cookbook', 'gallery')"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "Edit a gallery image: upscale, remove background, inpaint, or harmonize.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "Gallery image ID"},
                    "action": {"type": "string", "enum": ["upscale", "rembg", "inpaint", "harmonize"], "description": "Edit action"},
                    "prompt": {"type": "string", "description": "For inpaint: what to fill the masked area with"},
                    "scale": {"type": "number", "description": "For upscale: scale factor (default 2)"},
                },
                "required": ["image_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_research",
            "description": "Start a deep research task on a topic. Returns a task ID for tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research question or topic"},
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_research",
            "description": "List, read/open, or delete saved deep-research reports from the user's Library. Reports are owner-scoped; delete requires confirmed=true after explicit user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read", "delete"], "description": "list = show reports; read = return report text and sources; delete = remove a saved report after confirmation."},
                    "id": {"type": "string", "description": "Research report id from action=list, e.g. rp-abc123."},
                    "search": {"type": "string", "description": "Search query for action=list."},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_contact",
            "description": "Look up a contact by name. Searches CardDAV address book and sent email history. Returns email addresses (when available) or phone numbers. Use when the user says 'message [name]', 'email [name]', or asks for someone's contact details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name to search for"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_contact",
            "description": "Create, update, delete, or list the user's CardDAV contacts. Use to save a new contact, update an existing one (email/phone/address), or remove one. For update/delete you need the contact's uid — call action='list' first to find it. Delete requires confirmed=true after explicit user confirmation. Writes go through the same dedupe + validation as the Contacts UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "update", "delete"],
                               "description": "list = show all contacts (with uids); add = create; update = edit by uid; delete = remove by uid."},
                    "uid": {"type": "string", "description": "Contact UID (required for update/delete; get it from action=list)."},
                    "name": {"type": "string", "description": "Contact's display name (for add/update)."},
                    "email": {"type": "string", "description": "Single email address (convenience for add, or the primary email for update)."},
                    "emails": {"type": "array", "items": {"type": "string"}, "description": "Full list of email addresses (for update; first is primary)."},
                    "phones": {"type": "array", "items": {"type": "string"}, "description": "Full list of phone numbers (for update)."},
                    "address": {"type": "string", "description": "Postal/mailing address as a single human-readable string."},
                    "confirmed": {"type": "boolean", "description": "Required true for delete after explicit user confirmation."},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts",
            "description": "List configured email accounts. Use this before checking mail when the user names a mailbox/account such as Gmail, work, or a custom domain, then pass the returned account name/email/id to the other email tools.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send a new email. Use resolve_contact first if you only have a name and need to find the email address. If multiple accounts exist, pass account from list_email_accounts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body text"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_emails",
            "description": "List emails from an account/folder, newest first. Returns subject, sender, date, UID, and account for each email. Use list_email_accounts first when the user mentions Gmail/work/a custom mailbox. For last/latest/newest email requests, use max_results=1 and unread_only=false.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "max_results": {"type": "integer", "description": "Max emails to return (default: 20)"},
                    "limit": {"type": "integer", "description": "Backward-compatible alias for max_results"},
                    "unread_only": {"type": "boolean", "description": "Only show unread emails. Default false; set true only when the user asks for unread emails."},
                    "unresponded_only": {"type": "boolean", "description": "Only show unanswered emails. Default false."},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read the full content of a specific email by UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID to read"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": "SEND a reply email immediately by UID. Do not use this when the user asks to open/start a reply window or draft; use ui_control action=open_email_reply instead. For follow-up 'reply ...' requests where the user clearly wants to send now, use the exact UID from the latest read_email/list_emails result; never invent UID 1. Automatically threads with In-Reply-To/References headers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Exact UID of the email to reply to from list_emails/read_email; never invent UID 1"},
                    "body": {"type": "string", "description": "Reply body text"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "bulk_email",
            "description": "Perform one action on many emails at once. Use this for 'delete all those', 'archive these', 'mark all read', or any bulk operation after list_emails. Always pass account when the listed emails came from a named account such as Gmail. action=delete requires explicit user confirmation and confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"], "description": "Bulk action to perform"},
                    "uids": {"type": "array", "items": {"type": "string"}, "description": "UIDs from the latest list_emails result"},
                    "all_unread": {"type": "boolean", "description": "Operate on all unread messages in folder instead of explicit UIDs"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "For delete: hard-delete instead of moving to Trash"},
                    "confirmed": {"type": "boolean", "description": "Required for action=delete after explicit user confirmation"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_email",
            "description": "Delete one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail. permanent=true requires explicit user confirmation and confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "permanent": {"type": "boolean", "description": "Hard-delete instead of moving to Trash"},
                    "confirmed": {"type": "boolean", "description": "Required only for permanent=true after explicit user confirmation"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email",
            "description": "Archive one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_email_read",
            "description": "Mark one email as read or unread by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "read": {"type": "boolean", "description": "True marks read; false marks unread"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_bg_jobs",
            "description": "Inspect and control detached background `bash` jobs (started with the `#!bg` marker). action='list' shows this chat's jobs with id/status/age/command; action='output' returns a job's captured output so far (use for a still-running job, or to re-read a finished one); action='kill' terminates a runaway job's process tree instead of waiting out its max-runtime. output and kill need job_id from list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "output", "kill"], "description": "list | output | kill (default: list)"},
                    "job_id": {"type": "string", "description": "Background job id (required for output/kill; from action='list')"},
                },
                "required": ["action"]
            }
        }
    },
]


