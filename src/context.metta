;; create initial structures of root frame, frame, sub-frame, goal, frame-ref
;; create constructors and initializer
;; create helpers
;; TODO: initialize all states
;; Future scaling: make a function to clear certain frames from the completed frame space.

;; Note: currently your in a pickle of chosing to make the frame state based or value based
;; Solution 1: create the frame as pure values, then when it gets selected to be the current
;;             frame, you cache the parameters, which gets mutated, into a state and when the 
;;             frame becomes completed you map those states back to the frame then complete the frame.
;;
;; Solution 2: convert the frame creation to a space.

(= (cfv2ModeFast) Fast)
(= (cfv2ModeSlow) Slow)

(= (cfv2StatusActive) Active)
(= (cfv2StatusFocused) Focused)
(= (cfv2StatusSuspended) Suspended)
(= (cfv2StatusBlocked) Blocked)
(= (cfv2StatusCompleted) Completed)
(= (cfv2StatusFailed) Failed)
(= (cfv2StatusArchived) Archived)

(= (cfv2GoalStatusProposed) Proposed)
(= (cfv2GoalStatusActive) Active)
(= (cfv2GoalStatusBlocked) Blocked)
(= (cfv2GoalStatusSatisfied) Satisfied)
(= (cfv2GoalStatusFailed) Failed)
(= (cfv2GoalStatusSuspended) Suspended)
(= (cfv2GoalStatusRejected) Rejected)

(= (cfv2SourceUser) UserDirective)
(= (cfv2SourceAgent) AgentDirective)

(= (cfv2SpaceActive) Active)
(= (cfv2SpaceCompleted) Completed)

(= (cfv2PayloadLimit) 900)
(= (cfv2HistorySummaryLimit) 2400)
(= (cfv2LastResultsLimit) 1200)

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Python helper wrappers
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-now)
   (swrite (py-call (helper.cfv2_now))))

(= (cfv2-make-id $prefix)
   (py-call (helper.cfv2_make_id (repr $prefix))))

(= (cfv2-compact-plain $value)
   (py-call
      (helper.cfv2_compact_plain
         (repr $value)
         (cfv2LastResultsLimit))))

(= (cfv2-compact-limited $value $limit)
   (py-call
      (helper.cfv2_compact_plain
         (repr $value)
         $limit)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Defaults
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-default-method)
   (CertifiedMethod
      (status None)
      (description "")
      (parameters ())
      (evaluation-protocol ())
      (certificate-hash "")))

(= (cfv2-default-budget)
   (ResourceBudget
      (max-output-tokens (maxOutputToken))
      (max-command-lines 5)
      (wake-interval (wakeupInterval))
      (status Open)))

(= (cfv2-default-constraints)
   ((Constraint FrameIsAuthority
      "The current frame is the authoritative task state for the focused work item.")
    (Constraint RootIsPointerOnly
      "RootFrame stores only IDs, space names, global budget and global constraints; full frames live in spaces.")
    (Constraint NoRawThoughtPrompting
      "Do not rely on raw transcript history as working state.")
    (Constraint SkillCommandsOnly
      "The agent may invoke only commands listed in the skill set.")
    (Constraint AuditViaHistory
      "Exact chronological audit is available through history.metta, pin, episodes and runtime logs; frames store compact history summaries.")
    (Constraint NoUnjustifiedAutonomousSideEffects
      "Autonomous actions must serve an active current frame and respect budget.")))

(= (cfv2-default-modules)
   ((Entry send
      (ModuleProfile (kind Skill) (capabilities (RespondToUser)) (rating 1.0)))
    (Entry remember
      (ModuleProfile (kind Skill) (capabilities (WriteLongTermMemory)) (rating 1.0)))
    (Entry query
      (ModuleProfile (kind Skill) (capabilities (ReadLongTermMemory)) (rating 1.0)))
    (Entry episodes
      (ModuleProfile (kind Skill) (capabilities (ReadInteractionEpisodes)) (rating 1.0)))
    (Entry pin
      (ModuleProfile (kind Skill) (capabilities (WriteWorkingMemory)) (rating 1.0)))
    (Entry shell
      (ModuleProfile (kind Skill) (capabilities (ExecuteShell)) (rating 0.6)))
    (Entry read-file
      (ModuleProfile (kind Skill) (capabilities (ReadFile)) (rating 1.0)))
    (Entry write-file
      (ModuleProfile (kind Skill) (capabilities (WriteFile)) (rating 0.8)))
    (Entry append-file
      (ModuleProfile (kind Skill) (capabilities (AppendFile)) (rating 0.8)))
    (Entry search
      (ModuleProfile (kind Skill) (capabilities (WebSearch)) (rating 0.8)))
    (Entry tavily-search
      (ModuleProfile (kind Skill) (capabilities (AgentWebSearch)) (rating 0.8)))
    (Entry technical-analysis
      (ModuleProfile (kind Skill) (capabilities (TechnicalAnalysis)) (rating 0.7)))
    (Entry metta
      (ModuleProfile (kind Skill) (capabilities (EvaluateMeTTa)) (rating 0.7)))
    (Entry complete-goals-stm
      (ModuleProfile (kind FrameManagement) (capabilities (CompleteFocusedFrameToSTM)) (rating 1.0)))
    (Entry complete-goals-ltm
      (ModuleProfile (kind FrameManagement) (capabilities (CompleteFocusedFrameToLTM)) (rating 1.0)))
    (Entry clear-frame-junk
      (ModuleProfile (kind FrameManagement) (capabilities (ClearTransientFrameState)) (rating 1.0)))))

(= (cfv2-prompt-modules)
   ((Entry skill-set
      (ModuleProfile
         (kind SkillSet)
         (capabilities
            (send remember query episodes pin read-file write-file append-file search tavily-search technical-analysis
               metta new-frame new-autonomous-frame switch-frame switch-mode show-root-frame show-current-frame show-frame-index
               show-active-framespace show-completed-framespace complete-goals-stm complete-goals-ltm clear-frame-junk))
         (rating 1.0)))))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Constructors
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
(= (cfv2ActiveFrameSpace)
    (get-state &cfv2-active-framespace))
(= (cfv2CompletedFrameSpace)
    (get-state &cfv2-completed-framespace))
(= (cfv2FrameIndexSpace)
    (get-state &cfv2-frame-indexspace))

;; TODO: rename function to -> cfv2-root-frame
;; - change the current-frame-id getter to this function cfv2-root-current-frame-id
;; Update: both comments are addressed
(= (cfv2-root-frame)
   (RootFrame
      (id (get-state &cfv2-root-id))
      (current-frame-id (cfv2-root-current-frame-id))
      (active-framespace &cfv2-active-framespace)
      (completed-framespace &cfv2-completed-framespace)
      (frame-indexspace &cfv2-frame-indexspace)
      (mode (get-state &cfv2-root-mode))
      (global-budget (get-state &cfv2-global-budget))
      (global-constraints (get-state &cfv2-global-constraints))))

(= (cfv2-make-goal $goalID $frameID $subFrameID $description $source $priority $criteria)
   (Goal
      (goalID $goalID)
      (frameID $frameID)
      (sub-frameID $subFrameID)
      (description $description)
      (status Active)
      (source $source)
      (priority $priority)
      (dependencies ())
      (success-criteria $criteria)
      (created-at (cfv2-now))
      (completed-at ())
      (completion-summary ())))

(= (cfv2-make-deliverable $description)
   (Deliverable
      (id (cfv2-make-id Deliverable))
      (description $description)
      (status Pending)))


;; TODO: change the current-frame-id getter to this function cfv2-root-current-frame-id.
;;       Also change how current frame is set.
(= (cfv2-current-frame)
   (if (== (cfv2-root-current-frame-id) ())
       NO-CURRENT-FRAME-SET
      ; (cfv2-get-frame (cfv2-root-current-frame-id) Active)
      (Frame
        (frameID (cfv2-root-current-frame-id))
        (parent-frameID (get-state &cfv2-current-parent-frame-id))
        (source (get-state &cfv2-current-source))
        (priority (get-state &cfv2-current-priority))
        (goal-namespace (get-state &cfv2-current-goal-namespace))
        (status (get-state &cfv2-current-status))
        (frame-mode (get-state &cfv2-current-frame-mode))
        (hypotheses (get-state &cfv2-current-hypotheses))
        (method (get-state &cfv2-current-method))
        (history-summary (get-state &cfv2-current-history-summary))
        (modules (get-state &cfv2-current-modules))
        (budget (get-state &cfv2-current-budget))
        (constraints (get-state &cfv2-current-constraints))
        (deliverables (get-state &cfv2-current-deliverables))
        (sub-frame-namespace (get-state &cfv2-current-sub-frame-namespace))
        (results (get-state &cfv2-current-results))
        (created-at (get-state &cfv2-current-created-at))
        (updated-at (get-state &cfv2-current-updated-at))
        (completed-timestamp (get-state &cfv2-current-completed-timestamp))
        (completed-summary (get-state &cfv2-current-completed-summary)))
          ))


;; TODO: change the current-frame-id getter to this function cfv2-root-current-frame-id.
;; Update: the current frame ID is now taken from the root's current frame ID.
(= (cfv2-make-frame-ref-from-current $frameID $space)
    (let $currentFrameID (if (== () $frameID) (cfv2-root-current-frame-id) $frameID)
        (let*
            (
               ; ($frame (cfv2-get-frame $currentFrameID $space))
               ; ($_ (println! $frame))
               ((Frame $frameID' $parentFrameID $source $priority $goalNamespace $status $frameMode $hypotheses $method (history-summary $historySummary)
                     $modules $budget $constraints $deliverables $subFrameNamespace $results $createdAt $updatedAt $completedTimestamp $completedSummary)
                  (cfv2-get-frame $currentFrameID $space))

               ($_ (println! ("Creating frame reference for frameID: " $frameID " in space: " $space)))

            )
            (FrameRef
                (frameID $currentFrameID) $parentFrameID (space (if (== $space &cfv2-completed-framespace) Completed Active)) $source
                $priority $status $frameMode
                (summary (cfv2-compact-limited $historySummary (cfv2PayloadLimit)))
                $createdAt $updatedAt $completedTimestamp))))


;; TODO: wrong implementation
;; Update: The function now returns a ContextProjection object with the root frame only.
(= (cfv2-context-projection)
   (ContextProjection
      (RootFrame (cfv2-root-frame))
      (CurrentFrame (cfv2-current-frame))
   ))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Initialization
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-clear-current-frame-cache)
   (progn
      (change-state! &cfv2-current-frame-id ())
      (change-state! &cfv2-current-frame-id ())
      (change-state! &cfv2-current-parent-frame-id ())
      (change-state! &cfv2-current-source ())
      (change-state! &cfv2-current-priority 0.0)
      (change-state! &cfv2-current-goal-namespace ())
      (change-state! &cfv2-current-status ())
      (change-state! &cfv2-current-frame-mode ())
      (change-state! &cfv2-current-hypotheses ())
      (change-state! &cfv2-current-method (cfv2-default-method))
      (change-state! &cfv2-current-history-summary "")
      (change-state! &cfv2-current-modules (cfv2-default-modules)) ;; TODO: just call compact skill from skill.metta
      (change-state! &cfv2-current-budget (cfv2-default-budget))
      (change-state! &cfv2-current-constraints (cfv2-default-constraints))
      (change-state! &cfv2-current-deliverables ())
      (change-state! &cfv2-current-sub-frame-namespace ())
      (change-state! &cfv2-current-results ())
      (change-state! &cfv2-current-created-at ())
      (change-state! &cfv2-current-updated-at ())
      (change-state! &cfv2-current-completed-timestamp ())
      (change-state! &cfv2-current-completed-summary ())
      ; (change-state! &cfv2-current-goal ())
      CURRENT-FRAME-CACHE-CLEARED))

(= (cfv2-init-context-frames)
   (progn
      ;; Root state.
      (change-state! &cfv2-root-id (cfv2-make-id RootFrame))
      (change-state! &cfv2-root-mode Slow)
      (change-state! &cfv2-global-budget (cfv2-default-budget))
      (change-state! &cfv2-global-constraints (cfv2-default-constraints))
      (change-state! &cfv2-last-admitted-frame-id ())
      (change-state! &cfv2-current-frame-id ())
      

      ;; List-backed external spaces. These are intentionally not in RootFrame.
      (change-state! &cfv2-active-framespace ())
      (change-state! &cfv2-completed-framespace ())
      (change-state! &cfv2-frame-indexspace ())
      (change-state! &cfv2-goalspace ())
      (change-state! &cfv2-subframespace ())

      ;; Hot focused frame cache.
      (cfv2-clear-current-frame-cache)
      CONTEXT-FRAMES-V2-INITIALIZED))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Space append/index helpers
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-add-active-frame $frame)
   (let $currentSpace (if (== (get-state &cfv2-active-framespace) ()) 
      ((get-state &cfv2-active-framespace)) (get-state &cfv2-active-framespace))
      (change-state! &cfv2-active-framespace
         (append $currentSpace ($frame)))))

(= (cfv2-add-completed-frame $frame)
   (let $currentSpace (if (== (get-state &cfv2-completed-framespace) ()) 
      ((get-state &cfv2-completed-framespace)) (get-state &cfv2-completed-framespace))
      (change-state! &cfv2-completed-framespace
         (append $currentSpace ($frame)))))

(= (cfv2-add-frame-ref $ref)
   (let $currentSpace (if (== (get-state &cfv2-frame-indexspace) ()) 
      ((get-state &cfv2-frame-indexspace)) (get-state &cfv2-frame-indexspace))
      (change-state! &cfv2-frame-indexspace
         (append $currentSpace ($ref)))))

(= (cfv2-add-goal $goal $goalspace)
   (let $currentSpace (if (== (get-state $goalspace) ()) 
      ((get-state $goalspace)) (get-state $goalspace))
      (change-state! $goalspace
         (append $currentSpace ($goal)))))

(= (cfv2-add-sub-frame $subFrame $subframespace)
   (let $currentSpace (if (== (get-state $subframespace) ()) 
      ((get-state $subframespace)) (get-state $subframespace))
      (change-state! $subframespace
         (append $currentSpace ($subFrame)))))

;; TODO: change the way current frame is snapshotted.
;; Update: The snapshot function has changed to take a frame as an argument. The current frame is now snapshotted by passing a frame.
(= (cfv2-snapshot-current-frame $space $frame)
   ; (if (== (cfv2-root-current-frame-id) ())
   ;     NO-CURRENT-FRAME-TO-SNAPSHOT
       (if (== $space Completed)
           (cfv2-add-completed-frame $frame)
           (cfv2-add-active-frame $frame)))
         ;   )

(= (cfv2-index-current-frame $frameID $space)
   ; (if (== (cfv2-root-current-frame-id) ())
   ;     NO-CURRENT-FRAME-TO-INDEX
   (let $namespace (if (== $space Completed) &cfv2-completed-framespace &cfv2-active-framespace)
         (cfv2-add-frame-ref (cfv2-make-frame-ref-from-current $frameID $namespace))))
      ;  )

(= (cfv2-switch-mode)
   (change-state! &cfv2-root-mode
      (if (== (get-state &cfv2-root-mode) Fast) Slow Fast)))
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Root helpers
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
(= (cfv2-root-mode)
   (get-state &cfv2-root-mode))

(= (cfv2-set-root-mode $mode)
   (if (or (== $mode Fast) (== $mode Slow))
       (progn (change-state! &cfv2-root-mode $mode) (cfv2-root-frame))
       (InvalidRootMode $mode)))

(= (cfv2-root-current-frame-id)
   (get-state &cfv2-current-frame-id))

(= (cfv2-root-set-current-frame-id $frameID)
   (change-state! &cfv2-current-frame-id $frameID))


;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Current frame mutation helpers
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-touch-current-frame)
   (change-state! &cfv2-current-updated-at (cfv2-now)))

;; TODO: limit the size of the history summary to a certain number of characters or tokens
;; take in the frame then deconstruct the frame then change the state of that frame.
;; Update: Since the current frame cache has been set the history summary can be updated directly in the state without needing to deconstruct the frame.
(= (cfv2-update-current-history-summary $kind $summary)
    (let $new-summary
        (swrite ((get-state &cfv2-current-history-summary) | $kind : $summary))
        (change-state! &cfv2-current-history-summary $new-summary)))

;; TODO: The snapshot function has changed to take a frame as an argument.
(= (cfv2-record-frame-note $kind $payload)
   (if (== (get-state &cfv2-current-frame-id) ())
       NO-CURRENT-FRAME-FOR-COMMAND-BATCH
       (let $summary (cfv2-compact-plain $payload)
         (progn
            (cfv2-update-current-history-summary $kind $summary)
            (cfv2-touch-current-frame)
            (FRAME-NOTE-RECORDED)))))

(= (cfv2-record-command-batch $commands $results)
   (if (== (get-state &cfv2-current-frame-id) ())
       NO-CURRENT-FRAME-FOR-COMMAND-BATCH
      (let $resultsSummary (cfv2-compact-plain (append $commands $results))
         (progn
            ; (cfv2-update-current-history-summary CommandBatch $resultsSummary)
            (cfv2-touch-current-frame)
            (HISTORY-SUMMARY-UPDATED)))))

(= (cfv2-add-hypothesis $id $hypothesis)
   (progn
      (change-state! &cfv2-current-hypotheses
         (append (get-state &cfv2-current-hypotheses)
            ((Entry $id $hypothesis))))
      (cfv2-record-frame-note HypothesisAdded (Entry $id $hypothesis))))

;; TODO: Review required after full integration, On Entry $variant $metrics.
(= (cfv2-add-result $variant $metrics)
   (progn
      (change-state! &cfv2-current-results
         (append (get-state &cfv2-current-results)
            ((Entry $variant $metrics))))
      (cfv2-record-frame-note ResultAdded (Entry $variant $metrics))))

(= (cfv2-set-certified-method $description $parameters $evalProtocol $hash)
   (progn
      (change-state! &cfv2-current-method
         (CertifiedMethod
            (status Certified)
            (description $description)
            (parameters $parameters)
            (evaluation-protocol $evalProtocol)
            (certificate-hash $hash)))
      (cfv2-record-frame-note CertifiedMethodUpdated (get-state &cfv2-current-method))))


(= (cfv2-add-deliverable $description)
   (progn
      (println! (get-state &cfv2-current-deliverables))
      ; (change=state! &cfv2-current-deliverables ())
      (change-state! &cfv2-current-deliverables
         (append
            (get-state &cfv2-current-deliverables)
            ((cfv2-make-deliverable $description))))
      (println! ("changed state"))
      (cfv2-record-frame-note DeliverableAdded $description)
      ))

(= (cfv2-add-constraint $constraint)
   (progn
      (change-state! &cfv2-current-constraints
         (append (get-state &cfv2-current-constraints)
            ($constraint)))
      (cfv2-record-frame-note ConstraintAdded $constraint)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Frame creation and user-message ingestion
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; TODO: change the default method, modules, constraints, budget, etc; they are bloating the frame.
(= (cfv2-create-frame $source $description $priority $mode)
   (let* (($frameID (cfv2-make-id Frame))
          ($goalSpaceID (cfv2-make-id &GoalSpace))
          ($subFrameSpaceID (cfv2-make-id &SubFrameSpace))
          ($now (cfv2-now))
          ($deliverables $description)
          ($subframe (cfv2-create-sub-frame $frameID $subFrameSpaceID $goalSpaceID $description $source $priority $deliverables))
          ($frame (Frame
             (frameID $frameID)
             (parent-frameID ())
             (source $source)
             (priority $priority) ;; For future their should be heuristic to determine the priority of a frame based on the priority of its goal.
             (goal-namespace $goalSpaceID)
             (status Active)
             (frame-mode $mode)
             (hypotheses ())
             (method (cfv2-default-method))
             (history-summary (cfv2-compact-limited $description (cfv2HistorySummaryLimit)))
             (modules (cfv2-default-modules))
             (budget (cfv2-default-budget))
             (constraints ())
             (deliverables ($deliverables))
             (sub-frame-namespace $subFrameSpaceID)
             (results ())
             (created-at $now)
             (updated-at $now)
             (completed-timestamp ())
             (completed-summary ())
             ))
         )
      
      (progn
         (cfv2-snapshot-current-frame Active $frame)
         ; (println! (CREATING-FRAME $frameID FROM $source WITH-MODE $mode))

         (cfv2-index-current-frame $frameID Active)
         ; (println! (CREATING-FRAME $frameID FROM $source WITH-MODE $mode))

         (change-state! &cfv2-last-admitted-frame-id $frameID)
         ; (cfv2-current-frame) ;; Doesn't seem necessary to return the current frame.
         (FRAME-CREATED-AND-Stored-IN-ACTIVE-FRAME-SPACE)

         (NewframeID $frameID)

         )))

;; User's message should always be on the Fast loop.
(= (cfv2-create-frame-from-user-message $msg)
   (cfv2-create-frame UserDirective $msg 1.0 Fast))

(= (cfv2-create-autonomous-frame $description $priority $mode)
   (cfv2-create-frame AgentDirective $description $priority $mode))

(= (cfv2-record-message-for-current-frame $msg)
   (if (== (get-state &cfv2-current-frame-id) ())
      NO-CURRENT-FRAME-FOR-COMMAND-BATCH
      (progn
         (cfv2-update-current-history-summary UserMessageForCurrentFrame (cfv2-compact-plain $msg))
         (cfv2-touch-current-frame)
         MESSAGE-RECORDED-FOR-CURRENT-FRAME
         )))

;; TODO: There should be another function to update a frame with a user message if there is a frame already consisting
;;          ongoing frame for that specific user/module/sub-agents. 
(= (cfv2-ingest-user-message $msg)
       (cfv2-create-frame-from-user-message $msg))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; SubFrame creation
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-create-sub-frame $frameID $subFrameSpaceID $goalSpace $description $source $priority $deliverables)
       (let* (($subFrameID (cfv2-make-id SubFrame))
              ($goalID (cfv2-make-id Goal))
              ($goal (cfv2-make-goal $goalID $frameID $subFrameID
                        $description $source $priority $deliverables))
              ($subFrame
                 (SubFrame
                    (sub-frameID $subFrameID)
                    (frameID $frameID)
                    (priority $priority)
                    (status Active)
                    (goal $goal)
                    (deliverables $deliverables)
                    (dependencies ())
                    (created-at (cfv2-now))
                    (completed-at ())
                    (completion-summary ()))))
          (progn
            ; (println! ($goalspace))
            (change-state! $goalSpace ())
            (change-state! $subFrameSpaceID ())
            (cfv2-add-goal $goal $goalSpace)
            ; (println! ($goalSpace))
            (cfv2-add-sub-frame $subFrame $subFrameSpaceID)
            ; (println! ($subFrameSpaceID))
            $subFrame)))

(= (cfv2-complete-sub-frame $subFrameID $summary)
   (cfv2-record-frame-note SubFrameCompleted
      (SubFrameCompletion
         (sub-frameID $subFrameID)
         (summary $summary)
         (completed-at (cfv2-now)))))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Frame retrieval and switching
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

;; Alternative for cfv2-load-frame-atom and cfv2-switch-frame
(= (cfv2-load-frame $currentframeID)
    (let (Frame (frameID $frameID) (parent-frameID $parentID) (source $source)
                (priority $priority) (goal-namespace $goalSpace) (status $status)
                (frame-mode $mode) (hypotheses $hypotheses) (method $methods)
                (history-summary $historySummary) (modules $modules) (budget $budget)
                (constraints $constraint) (deliverables $deliverables)
                (sub-frame-namespace $subFrameSpaceID) (results $result)
                (created-at $createdAt) (updated-at $updatedAt) (completed-timestamp $completedTimestamp)
                (completed-summary $completedSummary)) 

        (cfv2-get-frame $currentframeID &cfv2-active-framespace)

        (progn
            (change-state! &cfv2-current-frame-id $currentframeID)
            (change-state! &cfv2-current-parent-frame-id $parentID)
            (change-state! &cfv2-current-source $source)
            (change-state! &cfv2-current-priority $priority)
            (change-state! &cfv2-current-goal-namespace $goalSpace)
            (change-state! &cfv2-current-status $status)
            (change-state! &cfv2-current-frame-mode $mode)
            (change-state! &cfv2-current-hypotheses $hypotheses)
            (change-state! &cfv2-current-method $methods)
            (change-state! &cfv2-current-history-summary $historySummary)
            (change-state! &cfv2-current-modules $modules)
            (change-state! &cfv2-current-budget $budget)
            (change-state! &cfv2-current-constraints $constraint)
            (change-state! &cfv2-current-deliverables $deliverables)
            (change-state! &cfv2-current-sub-frame-namespace $subFrameSpaceID)
            (change-state! &cfv2-current-results $result)
            (change-state! &cfv2-current-created-at $createdAt)
            (change-state! &cfv2-current-updated-at $updatedAt)
            (change-state! &cfv2-current-completed-timestamp $completedTimestamp)
            (change-state! &cfv2-current-completed-summary $completedSummary)
            
            (cfv2-current-frame) ;; return the frame state after loading
            )))

;; Build a Frame atom from the current-frame cache states.
(= (cfv2-current-cache-to-frame)
    (Frame
        (frameID (cfv2-root-current-frame-id))
        (parent-frameID (get-state &cfv2-current-parent-frame-id))
        (source (get-state &cfv2-current-source))
        (priority (get-state &cfv2-current-priority))
        (goal-namespace (get-state &cfv2-current-goal-namespace))
        (status (get-state &cfv2-current-status))
        (frame-mode (get-state &cfv2-current-frame-mode))
        (hypotheses (get-state &cfv2-current-hypotheses))
        (method (get-state &cfv2-current-method))
        (history-summary (get-state &cfv2-current-history-summary))
        (modules (get-state &cfv2-current-modules))
        (budget (get-state &cfv2-current-budget))
        (constraints (get-state &cfv2-current-constraints))
        (deliverables (get-state &cfv2-current-deliverables))
        (sub-frame-namespace (get-state &cfv2-current-sub-frame-namespace))
        (results (get-state &cfv2-current-results))
        (created-at (get-state &cfv2-current-created-at))
        (updated-at (get-state &cfv2-current-updated-at))
        (completed-timestamp (get-state &cfv2-current-completed-timestamp))
        (completed-summary (get-state &cfv2-current-completed-summary))))


;; TODO: make this generic for all use-case not only for frame ref
;; Update: The function is now generic for all use-cases, not only for frame ref.
;;         Also it now replaces the match implementation with state operation.
(= (cfv2-check-frame-id (Frame (frameID $currentID) $parentID $source $priority $goalNamespace $status $frameMode $hypotheses $method $historySummary
            $modules $budget $constraints $deliverables $subFrameNamespace $results $createdAt $updatedAt $completedTimestamp $completedSummary)
            $frameID)
    (if (== $currentID $frameID) True False))

;; A more generic approach as an alternative for cfv2-frame-by-id and cfv2_latest_frame_by_id
(= (cfv2-get-frame $frameID $space)
    (car-atom (filter-atom (get-state $space) $frame
        (== (cfv2-check-frame-id $frame $frameID) True))))

;; Removes a frame from a frame space by the given ID 
;; -> returns a list of frames without the frame with the given ID.
(= (cfv2-remove-frame $frameID $frameSpace)
    (filter-atom (get-state $frameSpace) $frame
        (not (== (cfv2-check-frame-id $frame $frameID) True))))

(= (cfv2-select-next-frame)
   (let $nextID
      (py-call
         (helper.cfv2_select_next_frame_id
            (repr (cfv2-frame-refs-by-status Active))
            (repr (get-state &cfv2-root-mode))))
      (if (== $nextID NON)
          (if (== (get-state &cfv2-root-mode) Fast)
              (progn
                 (change-state! &cfv2-root-mode Slow)
                 (let $nextSlowID
                    (py-call
                       (helper.cfv2_select_next_frame_id
                          (repr (cfv2-frame-refs-by-status Active))
                          (repr (get-state &cfv2-root-mode))))
                    (if (== $nextSlowID NON)
                        (progn
                           (cfv2-clear-current-frame-cache)
                           (NO-ACTIVE-FRAME-FOR-ROOT-MODE))
                        (progn
                           (println! (TYRING TO PRINT:  $nextSlowID))
                           (cfv2-load-frame $nextSlowID)))))
              (progn
                 (cfv2-clear-current-frame-cache)
                 (NO-ACTIVE-FRAME-FOR-ROOT-MODE)))
          (progn
             (println! (TYRING TO PRINT:  $nextID))
             (cfv2-load-frame $nextID)))))

(= (cfv2-check-ref-status (FrameRef $frameID $parentFrameID $space 
                $source $priority (status $currentStatus) $frameMode 
                $summary $createdAt $updatedAt $completedTimestamp) 
                $status)
    (if (== $currentStatus $status) True False)
)

(= (cfv2-frame-refs-by-status $status)
   (if (== (cfv2FrameIndexSpace) ()) (FRAME-REF-EMPTY)
    (filter-atom (cfv2FrameIndexSpace) $frameRef
        (== (cfv2-check-ref-status $frameRef $status) True)))
)

(= (cfv2-completed-frame-refs-after $datePrefix)
   (sread
      (py-call
         (helper.cfv2_refs_completed_after
            (repr (get-state &cfv2-frame-indexspace))
            (repr $datePrefix)))))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Completion / STM / LTM
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-completed-frame-memory $storage $summary)
   (py-str
      ("HyperClawFrameMemory "
       "kind=CompletedFrame "
       "storage=" $storage " "
       "time=" (cfv2-now) " "
       "frameID=" (get-state &cfv2-current-frame-id) " "
       "source=" (get-state &cfv2-current-source) " "
       "mode=" (get-state &cfv2-current-frame-mode) " "
       "priority=" (get-state &cfv2-current-priority) " "
       "deliverables=" (cfv2-compact-plain (get-state &cfv2-current-deliverables)) " "
       "method=" (cfv2-compact-plain (get-state &cfv2-current-method)) " "
       "results=" (cfv2-compact-plain (get-state &cfv2-current-results)) " "
       "history=" (cfv2-compact-plain (get-state &cfv2-current-history-summary)) " "
       "summary=" $summary)))

(= (cfv2-mark-current-frame-completed $summary)
   (progn
      (change-state! &cfv2-current-status Completed)
      (change-state! &cfv2-current-completed-timestamp (cfv2-now))
      (change-state! &cfv2-current-completed-summary $summary)
      (cfv2-update-current-history-summary FrameCompleted $summary)
      (cfv2-touch-current-frame)
      ; (cfv2-current-frame)
      ))

(= (cfv2-complete-current-frame-to-stm $summary)
   (if (== (get-state &cfv2-current-frame-id) ())
       NO-CURRENT-FRAME-TO-COMPLETE
       (let $memory (cfv2-completed-frame-memory STM $summary)
          (progn
             (pin $memory)
             (cfv2-mark-current-frame-completed $summary)
             (cfv2-snapshot-current-frame Completed (cfv2-current-cache-to-frame))
             (cfv2-index-current-frame () Completed)
             (change-state! &cfv2-active-framespace (cfv2-remove-frame (cfv2-root-current-frame-id) &cfv2-active-framespace))
             (change-state! &cfv2-frame-indexspace (cfv2-remove-frame (cfv2-root-current-frame-id) &cfv2-frame-indexspace))
             (cfv2-clear-current-frame-cache)
             (cfv2-select-next-frame)
             FRAME-COMPLETED-STORED-STM))))

(= (cfv2-complete-current-frame-to-ltm $summary)
   (if (== (get-state &cfv2-current-frame-id) ())
       NO-CURRENT-FRAME-TO-COMPLETE
       (let $memory (cfv2-completed-frame-memory LTM $summary)
          (progn
             (remember $memory)
             (cfv2-mark-current-frame-completed $summary)
             (cfv2-snapshot-current-frame Completed (cfv2-current-cache-to-frame))
             (cfv2-index-current-frame () Completed)
             (change-state! &cfv2-active-framespace (cfv2-remove-frame (cfv2-root-current-frame-id) &cfv2-active-framespace))
             (change-state! &cfv2-frame-indexspace (cfv2-remove-frame (cfv2-root-current-frame-id) &cfv2-frame-indexspace))
             (cfv2-clear-current-frame-cache)
             (cfv2-select-next-frame)
             FRAME-COMPLETED-STORED-LTM))))

;; skipped cfv2-comapact-current-frame and cfv2-clear-current-frame-junk
;; Reason: the first is not needed, not for now, the second is redundant
;;TODO: evaluate thorologly of clear-frame-junk, is it really needed if we have a clear cache function that is called when there is no current frame or after completion and switching to the next frame?
(= (cfv2-clear-current-frame-junk $summary)
   (if (== (get-state &cfv2-current-frame-id) ())
       NO-CURRENT-FRAME-TO-CLEAR
       (progn
          (change-state! &cfv2-current-hypotheses ())
          (change-state! &cfv2-current-results ())
          (cfv2-update-current-history-summary FrameJunkCleared $summary)
          (cfv2-touch-current-frame)
         ;  (cfv2-snapshot-current-frame Active (cfv2-current-cache-to-frame))
         ;  (cfv2-index-current-frame Active)
          FRAME-JUNK-CLEARED)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Maintenance
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (cfv2-has-current-frame)
   (not (== (get-state &cfv2-current-frame-id) ())))

;; TODO: implement compact frame retrival
(= (cfv2-maintain-frame)
   (if (cfv2-has-current-frame)
       (if (and (== (get-state &cfv2-root-mode) Fast)
               (== (get-state &cfv2-current-frame-mode) Slow))
           ;; Root switched to Fast but current frame is Slow — yield to Fast frames
           (cfv2-select-next-frame)
           (progn
              (cfv2-touch-current-frame)
              (cfv2-current-frame)))
       (cfv2-select-next-frame)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; User-facing / skill-friendly helpers
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(= (new-frame $description)
   (cfv2-create-frame-from-user-message $description))

(= (new-autonomous-frame $description)
   (cfv2-create-autonomous-frame $description 0.5 Slow))

(= (switch-frame $frameID)
   (let $_ (cfv2-load-frame $frameID)
      (if (cfv2-has-current-frame)
          (cfv2-current-frame)
          (SWITCH-FRAME-FAILED $frameID))))

(= (show-root-frame)
   (cfv2-root-frame))

(= (show-current-frame)
   (cfv2-current-frame))

(= (show-frame-index)
   (get-state &cfv2-frame-indexspace))

(= (show-active-framespace)
   (get-state &cfv2-active-framespace))

(= (show-completed-framespace)
   (get-state &cfv2-completed-framespace))

(= (switch-mode)
   (cfv2-switch-mode))

; (= (create-sub-frame $description)
;    (cfv2-create-sub-frame $description 0.8 (cfv2-make-deliverable $description)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Backward compatibility with previous context.metta / loop.metta
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;


(= (initContextFrame)
   (cfv2-init-context-frames))

(= (currentRootFrame)
   (cfv2-root-frame))

(= (currentContextFrame)
   (cfv2-current-frame))

(= (contextFrameForPrompt)
   (swrite (cfv2-context-projection)))

;; TODO: this implementation needs to change see comment on cfv2-ingest-user-message
(= (ctx-ingest-user-message $msg)
   (cfv2-ingest-user-message $msg))

(= (ctx-record-command-batch $commands $results)
   (cfv2-record-command-batch $commands $results))

(= (ctx-maintain-frame)
   (cfv2-maintain-frame))

(= (ctx-has-active-goals)
   (cfv2-has-current-frame))

(= (ctx-compact-plain $value)
   (cfv2-compact-plain $value))

(= (ctx-add-hypothesis $id $hypothesis)
   (cfv2-add-hypothesis $id $hypothesis))

(= (ctx-add-result $variant $metrics)
   (cfv2-add-result $variant $metrics))

(= (ctx-set-certified-method $description $parameters $evalProtocol $hash)
   (cfv2-set-certified-method $description $parameters $evalProtocol $hash))

(= (ctx-add-deliverable $artifact)
   (cfv2-add-deliverable $artifact))

(= (complete-goals-stm $summary)
   (cfv2-complete-current-frame-to-stm $summary))

(= (complete-goals-ltm $summary)
   (cfv2-complete-current-frame-to-ltm $summary))

(= (clear-frame-junk $summary)
   (cfv2-clear-current-frame-junk $summary))