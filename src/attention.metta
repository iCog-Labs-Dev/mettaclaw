(= (ad-build-slice $fields)
   (py-call (modules.build_slice_from_metta
               (repr $fields)
               (contextFrameForPrompt))))

(= (ad-list-modules)
   (py-call (modules.list_modules_formatted)))

;; Directive hot cache
(= (ad-clear-last-directive)
   (progn
      (change-state! &ad-last-target    "")
      (change-state! &ad-last-task      "")
      (change-state! &ad-last-gate      "")
      (change-state! &ad-last-criteria  "")
      (change-state! &ad-last-priority  0.0)
      (change-state! &ad-last-output    "")
      (change-state! &ad-last-code      "")
      (change-state! &ad-last-admitted  ())
      (change-state! &ad-last-reason    "")
      (change-state! &ad-last-attempts  0)
      AD-DIRECTIVE-CACHE-CLEARED))

(= (ad-record-last-directive $target $task $gate $criteria $priority
                              $output $admitted $reason $attempts)
   (progn
      (change-state! &ad-last-target    $target)
      (change-state! &ad-last-task      $task)
      (change-state! &ad-last-gate      $gate)
      (change-state! &ad-last-criteria  $criteria)
      (change-state! &ad-last-priority  $priority)
      (change-state! &ad-last-output    $output)
      (change-state! &ad-last-admitted  $admitted)
      (change-state! &ad-last-reason    $reason)
      (change-state! &ad-last-attempts  $attempts)
      AD-DIRECTIVE-RECORDED))

(= (ad-last-directive)
   (LastDirective
      (target    (get-state &ad-last-target))
      (task      (get-state &ad-last-task))
      (gate      (get-state &ad-last-gate))
      (criteria  (get-state &ad-last-criteria))
      (priority  (get-state &ad-last-priority))
      (output    (get-state &ad-last-output))
      (admitted  (get-state &ad-last-admitted))
      (reason    (get-state &ad-last-reason))
      (attempts  (get-state &ad-last-attempts))))

;; Execution
(= (ad-record-to-frame $note $variant $metrics $admitted)
   (if (== (cfv2-has-current-frame) True)
       (progn
          (cfv2-record-frame-note DirectiveExecuted $note)
          (if (== $admitted "True")
              (cfv2-add-result $variant $metrics)
              _))
       _))

(= (execute-last-code)
   (let* (($raw      (get-state &ad-last-output))
          ($code     (py-call (helper.strip_code_fences (repr $raw))))
          ($result   (py-call (modules.execute_directive_from_metta
                                 (repr "python-executor")
                                 (repr $code)
                                 (repr "Execute")
                                 (repr "nonempty")
                                 (repr "")
                                 (repr 1))))
          ($output   (py-call (modules.directive_result_field $result "output")))
          ($admitted (py-call (modules.directive_result_field $result "admitted")))
          ($reason   (py-call (modules.directive_result_field $result "gate_reason")))
          ($attempts (py-call (modules.directive_result_field $result "attempts"))))
      (progn
         (change-state! &ad-last-code $code)
         (ad-record-last-directive
            "python-executor" "Execute" "nonempty" "" 1.0
            $output $admitted $reason $attempts)
         (ad-record-to-frame
            (py-str ("target=python-executor task=Execute"
                     " admitted=" $admitted
                     " attempts=" $attempts
                     " reason=" $reason))
            ExecutionOutput
            (py-str ("output=" $output " attempts=" $attempts))
            $admitted)
         (DirectiveResult
            (admitted $admitted)
            (output   $output)
            (reason   $reason)
            (attempts $attempts)))))

(= (dispatch-directive $args)
   (let* (($result   (py-call (modules.dispatch_directive_from_metta
                                 (repr $args)
                                 (contextFrameForPrompt))))
          ($output   (py-call (modules.directive_result_field $result "output")))
          ($admitted (py-call (modules.directive_result_field $result "admitted")))
          ($reason   (py-call (modules.directive_result_field $result "gate_reason")))
          ($attempts (py-call (modules.directive_result_field $result "attempts"))))
      (progn
         (change-state! &ad-last-code "")
         (ad-record-last-directive
            "dispatch" "" "" "" 1.0
            $output $admitted $reason $attempts)
         (ad-record-to-frame
            (py-str ("admitted=" $admitted
                     " attempts=" $attempts
                     " reason=" $reason))
            DirectiveOutput
            (py-str ("output=" $output " attempts=" $attempts))
            $admitted)
         (DirectiveResult
            (admitted $admitted)
            (output   $output)
            (reason   $reason)
            (attempts $attempts)))))

;; Agent-callable skills
(= (getDirectiveModules)
   (py-str
      ("AVAILABLE_DIRECTIVE_MODULES:" (newline)
       (ad-list-modules) (newline)
       "AVAILABLE_GATES: python-syntax | metta-syntax | math-result | nonempty | llm-judge | passthrough" (newline)
       "AVAILABLE_TASKS: Generate | Critique | Revise | Execute | Evaluate")))

(= (show-last-directive)
   (ad-last-directive))

(= (get-last-directive-output)
   (get-state &ad-last-output))

(= (last-directive-admitted)
   (get-state &ad-last-admitted))

(= (send-directive-output $intro)
   (if (== (get-state &ad-last-admitted) "False")
       (send (py-str ("Directive failed: " (get-state &ad-last-reason))))
       (if (== (get-state &ad-last-code) "")
           ;; No code was executed — send output directly
           (send (py-str ($intro (newline) (get-state &ad-last-output))))
           ;; Code was executed — show code block and execution result
           (send (py-str ($intro (newline)
                          "```python" (newline)
                          (get-state &ad-last-code) (newline)
                          "```" (newline)
                          "Output:" (newline)
                          (get-state &ad-last-output)))))))

;; Initialization
(= (ad-init)
   (progn
      (ad-clear-last-directive)
      (println! "Initializing attention directives")
      ATTENTION-DIRECTIVES-INITIALIZED))

(= (initAttentionDirectives)
   (ad-init))
