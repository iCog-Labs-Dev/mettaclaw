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
      (change-state! &ad-last-admitted  False)
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
       (if (== $admitted True)
           (progn
              (cfv2-record-frame-note DirectiveAdmitted $note)
              (cfv2-add-result $variant $metrics))
           (cfv2-record-frame-note DirectiveRejected $note))
       _))

(= (execute-last-code)
   (if (== (get-state &ad-last-output) "")
       (DirectiveResult (admitted False) (output "") (reason "no code to execute") (attempts 0))
   (let* (($raw      (get-state &ad-last-output))
          ($code     (py-call (helper.strip_code_fences (repr $raw))))
          ($result   (py-call (modules.execute_directive_from_metta
                                 (repr "python-executor")
                                 (repr $code)
                                 (repr "Execute")
                                 (repr "exec-success")
                                 (repr "")
                                 (repr 1))))
          ($output   (py-call (modules.directive_result_field $result "output")))
          ($admitted (py-call (modules.directive_result_field $result "admitted")))
          ($reason   (py-call (modules.directive_result_field $result "gate_reason")))
          ($attempts (py-call (modules.directive_result_field $result "attempts"))))
      (progn
         (change-state! &ad-last-code $code)
         (ad-record-last-directive
            "python-executor" "Execute" "exec-success" "" 1.0
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
            (attempts $attempts))))))

(= (dispatch-directive $args)
   (let* (($args_str (py-call (modules.dispatch_directive_args_from_metta (repr $args))))
          ($target   (py-call (modules.directive_args_field $args_str "target")))
          ($task     (py-call (modules.directive_args_field $args_str "task")))
          ($gate     (py-call (modules.directive_args_field $args_str "gate")))
          ($criteria (py-call (modules.directive_args_field $args_str "criteria")))
          ($priority (py-call (modules.directive_args_field $args_str "priority")))
          ($result   (py-call (modules.dispatch_directive_from_metta
                                 (repr $args)
                                 (contextFrameForPrompt))))
          ($output   (py-call (modules.directive_result_field $result "output")))
          ($admitted (py-call (modules.directive_result_field $result "admitted")))
          ($reason   (py-call (modules.directive_result_field $result "gate_reason")))
          ($attempts (py-call (modules.directive_result_field $result "attempts"))))
      (progn
         (change-state! &ad-last-code "")
         (ad-record-last-directive
            $target $task $gate $criteria $priority
            $output $admitted $reason $attempts)
         (ad-record-to-frame
            (py-str ("target=" $target " task=" $task
                     " gate=" $gate
                     " admitted=" $admitted
                     " attempts=" $attempts
                     " reason=" $reason))
            DirectiveOutput
            (py-str ("target=" $target " task=" $task
                     " output=" $output
                     " attempts=" $attempts))
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
       "AVAILABLE_GATES: python-syntax | metta-syntax | math-result | nonempty | exec-success | task-consistency | passthrough" (newline)
       "AVAILABLE_TASKS: Generate | Critique | Revise | Execute | Evaluate" (newline)
       "FORMAT: dispatch-directive target task gate \"criteria\" priority slice  (criteria must be quoted, use \"\" for none)")))
(= (show-last-directive)
   (ad-last-directive))

(= (last-directive-admitted)
   (get-state &ad-last-admitted))

(= (send-directive-output $intro)
   (if (== (get-state &ad-last-admitted) ())
       (send "No directive has been run yet.")
   (if (== (get-state &ad-last-admitted) False)
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
                          (get-state &ad-last-output))))))))

;; Initialization
(= (ad-init)
   (progn
      (ad-clear-last-directive)
      (println! "Initializing attention directives")
      ATTENTION-DIRECTIVES-INITIALIZED))

(= (initAttentionDirectives)
   (ad-init))
