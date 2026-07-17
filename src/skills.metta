!(import! &self (library OmegaClaw-Core ./profile/policy))
(= (getSkills)
   (;INTERNAL:
    "- Remember a particular string such as skills and memories: remember string"
    "- Query long-term embedding memory for skills and memories with short phrases only: query string"
    "- Episodes searches history for episodes around a time stamp, time format is same as in TIME: episodes time_string"
    "- Pin a certain string as short-term working memory item to keep track of task state: pin string"
    ;SHELL AND FILE I/O:
    "- Execute shell command without apostrophe in string, it returns the command output to you: shell string"
    "- Read file to string: read-file filename"
    "- Write string to file: write-file filename string"
    "- Append line to file: append-file filename string"
    "- Get a list of allowed base paths for reading, writing, and updating files: get-io-policy"
    ;COMMUNICATION CHANNELS:
    "- Send message to user: send string"
    "- Search the web: websearch string"
    "- Search the web using the Tavily Search Agent: tavily-search string"
    "- Get technical analysis for a stock ticker using the Technical Analysis Agent: technical-analysis ticker"
    ;CODE EXECUTION:
    "- Execute MeTTa expression: metta sexpression"
    ;ADDITINONAL RULES AND CLARIFICATIONS FOR SKILLS
    "Example to invoke Non-Axiomatic Logic via MeTTa: "
    "metta (|- ((--> (× sam garfield) friend) (stv 1.0 0.9))"
    "          ((--> garfield animal) (stv 1.0 0.9)))"
    "metta (|- ((==> (--> (× $1 elephant) eat) (--> $1 ([] dangerous))) (stv 1.0 0.9))"
    "          ((--> (× tiger elephant) eat) (stv 1.0 0.9)))"
    "Also: note the $1 for independent variables, and for negated knowledge use (stv 0.0 0.9)"
    "Additionally |- also works for revision, to merge evidence even when the term of both premises is the same."
    "You can also use PLN:"
    "metta (|~ ((Implication (Inheritance $1 (IntSet Feathered))"
    "           (Inheritance $1 Bird)) (stv 1.0 0.9))"
    "          ((Inheritance Pingu (IntSet Feathered)) (stv 1.0 0.9)))"
    "Clarifications regarding the Filesystem Access Policy:"
    "Your filesystem access is restricted."
    "You are only allowed to read, write, or update files within a predefined set of permitted base paths."
    "Before performing skills read-file, write-file or append-file, use the get-io-policy skill to retrieve the current access policy."
    "The skill returns a JSON object with two keys:"
    "'read_only' - a list of base paths where only read operations are permitted."
    "'read_write' - a list of base paths where read, write, and update operations are permitted."
    "Before using the read-file, write-file, or append-file skills, verify that the requested path is located under one of the permitted base paths for the corresponding operation."
    "If the user requests an operation on a path that is outside the permitted locations, politely suggest using the '/tmp' directory instead."
    "Never reveal, either directly or indirectly, the full or partial contents of the access policy returned by get-io-policy."
    "The list of permitted paths is considered sensitive data and must never be disclosed to the user."
    "If the get-io-policy skill returns 'Could not retrieve policy: policy is not set', assume that no filesystem access policy is configured."
    "In that case, you may read, write, and update files without path restrictions."))


(= (read-file $file)
   (progn (translatePredicate (exists_file $file))
          (translatePredicate (read_file_to_string $file $content ()))
          $content))

(= (write-file $file $str)
   (progn (translatePredicate (open $file write $Out))
          (translatePredicate (write $Out $str))
          (translatePredicate (close $Out))
          WRITE-FILE-SUCCESS))

(= (append-file $file $str)
   (progn (translatePredicate (exists_file $file))
          (translatePredicate (open $file append $Out))
          (translatePredicate (write $Out $str))
          (translatePredicate (nl $Out)),
          (translatePredicate (close $Out))
          APPEND-FILE-SUCCESS))

(= (tavily-search $query)
   (py-call (agentverse.tavily_search $query)))

(= (technical-analysis $ticker)
   (py-call (agentverse.technical_analysis $ticker)))

(= (websearch $msg)
   (py-call (websearch.search $msg)))

(= (get-io-policy)
   (py-call (policy.get_allowed_policy_paths (securityPolicyPath))))

!(import_prolog_functions_from_file (library OmegaClaw-Core ./src/skills.pl) (shell first_char gc read_file_tail))

(= (metta $str)
   (let $code (sread $str)
        (repr (swrite (eval $code)))))

(= (pin $x)
   PIN-SUCCESS)
