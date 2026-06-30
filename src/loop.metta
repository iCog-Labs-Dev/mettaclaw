(= (maxNewInputLoops) (empty))
(= (maxWakeLoops) (empty))
(= (sleepInterval) (empty))
(= (LLM) (empty))
(= (provider) (empty))
(= (maxOutputToken) (empty))
(= (reasoningMode) (empty))
(= (wakeupInterval) (empty))
(= (spamShield) (empty))

(= (useFrames) (empty))

(= (initLoop)
   (progn (println! "=== OmegaClaw Configuration ===")
          (configure maxNewInputLoops 50)
          (configure maxWakeLoops 1)
          (configure spamShield True)
          (configure sleepInterval 1)
          (configure LLM gpt-5.4)
          (configure provider Anthropic) ;Anthropic or OpenAI or ASICloud or ASIOne
          (configure maxOutputToken 6000)
          (configure reasoningMode medium)
          (configure wakeupInterval 600) ;600=10 minutes
          (change-state! &prevmsg "")
          (change-state! &lastresults "")
          (change-state! &loops (maxNewInputLoops))
          (configure useFrames True)
          (py-call (lib_llm_ext.setActiveProvider (provider) (LLM)))
          (py-call (lib_llm_ext.setJudgeProvider "OpenAI"))
          ))


(= (getContext)
   (string-safe
      (py-str
         ("PROMPT: " (getPrompt) (newline)
          "CURRENT_CONTEXT_FRAME_S_EXPR: " (contextFrameForPrompt) (newline)
          "SKILL_SET: " (getSkills) (newline)
          "CONTEXT_FRAME_SKILLS: " (contextFramesSkills) (newline)
          "DIRECTIVE_MODULES: " (getDirectiveModules) (newline)
          "OUTPUT_FORMAT: Up to 5 skill command lines, do not wrap quotes around args, do not use variables:" (newline)
          "toolName1 arg1" (newline)
          "toolName2 arg2" (newline)
          "toolName3 arg3" (newline)
          "toolName4 arg4" (newline)
          "toolName5 arg5" (newline)
          "LAST_SKILL_USE_RESULTS: " (get-state &lastresults) (newline)
          "TIME: " (get_time_as_string)))))

(= (getLegacyContext)
   (string-safe
      (py-str
         ("PROMPT: " (getPrompt) (newline)
          "SKILL_SET: " (getSkills) (newline)
          "OUTPUT_FORMAT: Up to 5 skill command lines, do not wrap quotes around args, do not use variables:" (newline)
          "toolName1 arg1" (newline)
          "toolName2 arg2" (newline)
          "toolName3 arg3" (newline)
          "toolName4 arg4" (newline)
          "toolName5 arg5" (newline)
          "LAST_SKILL_USE_RESULTS: " (get-state &lastresults) (newline)
          "TIME: " (get_time_as_string)))))



(= (HandleError $msg $cmd $sexpr)
   (case $sexpr (((Error $a $b) (let $new (append (get-state &error) (($msg $cmd)))
                                          (change-state! &error $new)))
                 ($else $sexpr))))

(= (metta $str)
   (let $code (sread $str)
        (repr (swrite (eval $code)))))

(= (omegaclaw)
   (omegaclaw 1))
(= (omegaclaw $k)
   (progn
      (if (== $k 1)
          (progn
             (initLoop)
             (initMemory)
             (initChannels)
             (if (== (useFrames) True) (initContextFrame) _)
             (initAttentionDirectives))
          (change-state! &loops (- (get-state &loops) 1)))

      (println! (---------iteration $k))

      (let* (($msgrcv (string-safe (repr (receive))))
             ($msgnew
                (prog1
                   (and (> (string_length $msgrcv) 0)
                        (!= $msgrcv (get-state &prevmsg)))
                   (if (> (string_length $msgrcv) 0)
                       (change-state! &prevmsg $msgrcv)
                       _)))
             ($msg (get-state &prevmsg))

             ; New input becomes frame state before prompting.
             ($_ (if (and $msgnew (== (useFrames) True))
                    (progn
                       (ctx-ingest-user-message $msg)
                       (cfv2-load-frame (get-state &cfv2-last-admitted-frame-id)))
                    _))
              
            ($_ (if (and (== (useFrames) True) (not (ctx-has-active-goals)))
                     (ctx-maintain-frame) _))

             ($_ (if (and (> $k 1) $msgnew)
                    (progn 
                        (change-state! &loops (maxNewInputLoops))
                        (if (and (== (useFrames) True) (== (get-state &cfv2-root-mode) Slow)) (switch-mode) _))
                    _))

             ; Prompt is now frame-based.
             ($prompt (if (== (useFrames) True) (getContext) (getLegacyContext))))

         (if (> (get-state &loops) 0)
             (let* (($lastmessage
                       (if $msgnew
                           (if (== (useFrames) True)
                           "NEW_INPUT_HAS_BEEN_ADMITTED_AS_ACTIVE_FRAME. Continue current frame or switch-frame to a higher-priority admitted frame if appropriate."
                           ; (HUMAN-MSG: $msg) 
                           )
                           (if (and (spamShield) (== (useFrames) True))
                               "NO_NEW_INPUT. Continue only if the context frame contains an active useful goal or select relevant frame from the active space DO NOT RE-SEND OR SPAM!"
                               "DO NOT RE-SEND OR SPAM!")))
                    ($_ (change-state! &nextWakeAt (+ (get_time) (wakeupInterval))))
                    ($_ (println! $lastmessage))
                    ($send (py-str ($prompt :-:-:-: $lastmessage)))
                    ($_ (println! (CHARS_SENT: (string_length $send) $send)))

                    ($respi
                       (if (== (provider) OpenAI)
                           (useGPT (LLM) (maxOutputToken) (reasoningMode) $send)
                           (py-call (lib_llm_ext.callProvider
                                      (provider)
                                      $send
                                      (maxOutputToken)))))

                    ($resp (py-call (helper.balance_parentheses $respi)))
                    ($response
                       (if (== "(" (first_char $resp))
                           $resp
                           (progn
                              (println! $resp)
                              (repr
                                 (REMEMBER:OUTPUT_NOTHING_ELSE_THAN:
                                    ((skill arg) ...))))))

                    ($sexpr (catch (sread $response)))
                    ($_ (change-state! &error ()))
                    ($_ (HandleError
                           MULTI_COMMAND_FAILURE_NOTHING_WAS_DONE_PLEASE_CORRECT_PARENTHESES_AND_USE_QUOTES_AND_RETRY
                           $response
                           $sexpr))
                    ($_ (println! (RESPONSE: $sexpr)))

                    ($results
                       (RESULTS:
                          (collapse
                             (let $s (superpose $sexpr)
                                (COMMAND_RETURN:
                                   ($s
                                    (HandleError
                                       SINGLE_COMMAND_FORMAT_ERROR_NOTHING_WAS_DONE_PLEASE_FIX_AND_RETRY
                                       $s
                                       (catch (let $R (eval $s) (py-call (helper.normalize_string $R)))))))))))

                    ($_ (println! $results)))

                (progn
                   ; Legacy audit log remains optional.
                   (if (or $msgnew (not (== $sexpr ())))
                       (addToHistory $msg $response $sexpr $msgnew)
                       _)
                   ; Frame-native audit log.
                   (if (== (useFrames) True)
                     (progn
                        (ctx-record-command-batch $sexpr $results)
                        (ctx-maintain-frame))
                     _)
                   ; Compatibility state.
                   (change-state! &lastresults
                     (string-safe (py-call (helper.compact_plain (repr $results) 1200))))))

             (if (> (get_time) (get-state &nextWakeAt))
                 (change-state! &loops (+ 1 (maxWakeLoops)))
                 (if (== (get-state &cfv2-root-mode) Fast) (switch-mode) _))))

      (sleep (sleepInterval))
                      (cut)
                      (gc)
      (omegaclaw (+ 1 $k))))