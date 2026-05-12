
(= (initContextFrame)
   (progn
      (change-state! &ctx-goals ())
      (change-state! &ctx-mode Idle)
      (change-state! &ctx-hypotheses ())
      (change-state! &ctx-method
         (CertifiedMethod
            (status None)
            (description "")
            (parameters ())
            (evaluation-protocol ())
            (certificate-hash "")))
      (change-state! &ctx-history ())
      (change-state! &ctx-results ())
      (change-state! &ctx-modules (defaultContextModules))
      (change-state! &ctx-budget
         (ResourceBudget
            (max-output-tokens (maxOutputToken))
            (max-command-lines 5)
            (wake-interval (wakeupInterval))
            (status Open)))
      (change-state! &ctx-deliverables ())
      (change-state! &ctx-constraints (defaultContextConstraints))
      CONTEXT-FRAME-INITIALIZED))

(= (defaultContextConstraints)
   ((Constraint FrameIsAuthority
      "The context frame is the authoritative task state.")
    (Constraint NoRawThoughtPrompting
      "Do not rely on raw transcript history as working state.")
    (Constraint SkillCommandsOnly
      "The agent may invoke only commands listed in the skill set.")
    (Constraint AuditEveryCommand
      "Every command batch and result must be recorded in frame history.")
    (Constraint NoUnjustifiedAutonomousSideEffects
      "Autonomous actions must serve an active goal and respect budget.")))

(= (defaultContextModules)
   ((Entry send
      (ModuleProfile
         (kind Skill)
         (capabilities (RespondToUser))
         (rating 1.0)))
    (Entry remember
      (ModuleProfile
         (kind Skill)
         (capabilities (WriteLongTermMemory))
         (rating 1.0)))
    (Entry query
      (ModuleProfile
         (kind Skill)
         (capabilities (ReadLongTermMemory))
         (rating 1.0)))
    (Entry episodes
      (ModuleProfile
         (kind Skill)
         (capabilities (ReadInteractionEpisodes))
         (rating 1.0)))
    (Entry pin
      (ModuleProfile
         (kind Skill)
         (capabilities (WriteWorkingMemory))
         (rating 1.0)))
    (Entry shell
      (ModuleProfile
         (kind Skill)
         (capabilities (ExecuteShell))
         (rating 0.6)))
    (Entry read-file
      (ModuleProfile
         (kind Skill)
         (capabilities (ReadFile))
         (rating 1.0)))
    (Entry write-file
      (ModuleProfile
         (kind Skill)
         (capabilities (WriteFile))
         (rating 0.8)))
    (Entry append-file
      (ModuleProfile
         (kind Skill)
         (capabilities (AppendFile))
         (rating 0.8)))
    (Entry search
      (ModuleProfile
         (kind Skill)
         (capabilities (WebSearch))
         (rating 0.8)))
    (Entry tavily-search
      (ModuleProfile
         (kind Skill)
         (capabilities (AgentWebSearch))
         (rating 0.8)))
    (Entry technical-analysis
      (ModuleProfile
         (kind Skill)
         (capabilities (TechnicalAnalysis))
         (rating 0.7)))
    (Entry metta
      (ModuleProfile
         (kind Skill)
         (capabilities (EvaluateMeTTa))
         (rating 0.7)))))

(= (currentContextFrame)
   (ContextFrame
      (goals (Set (get-state &ctx-goals)))
      (mode (get-state &ctx-mode))
      (hypotheses (Map (get-state &ctx-hypotheses)))
      (method (get-state &ctx-method))
      (history (List (get-state &ctx-history)))
      (results (Map (get-state &ctx-results)))
      (modules (Map (get-state &ctx-modules)))
      (budget (get-state &ctx-budget))
      (deliverables (Checklist (get-state &ctx-deliverables)))
      (constraints (Set (get-state &ctx-constraints)))))

(= (contextFrameForPrompt)
   (swrite (currentContextFrame)))

(= (ctx-id $prefix)
   (py-str ($prefix "-" (get_time_as_string))))

(= (ctx-record-history $kind $payload)
   (let $record
      (ExperimentRecord
         (id (ctx-id Event))
         (time (get_time_as_string))
         (kind $kind)
         (payload $payload))
      (progn
         (change-state! &ctx-history
            (append (get-state &ctx-history) ($record)))
         $record)))

(= (ctx-ingest-user-message $msg)
   (let $goal
      (Motive
         (id (ctx-id Goal))
         (source UserDirective)
         (priority 1.0)
         (status Active)
         (text $msg)
         (created-at (get_time_as_string)))
      (progn
         (change-state! &ctx-mode GoalDirected)
         (change-state! &ctx-goals
            (append (get-state &ctx-goals) ($goal)))
         (ctx-record-history UserMessage $msg)
         (currentContextFrame))))

(= (ctx-record-command-batch $commands $results)
   (let $record
      (ExperimentRecord
         (id (ctx-id CommandBatch))
         (time (get_time_as_string))
         (kind CommandBatch)
         (commands $commands)
         (results $results))
      (progn
         (change-state! &ctx-history
            (append (get-state &ctx-history) ($record)))
         (currentContextFrame))))

(= (ctx-add-hypothesis $id $hypothesis)
   (progn
      (change-state! &ctx-hypotheses
         (append (get-state &ctx-hypotheses)
            ((Entry $id $hypothesis))))
      (currentContextFrame)))

(= (ctx-add-result $variant $metrics)
   (progn
      (change-state! &ctx-results
         (append (get-state &ctx-results)
            ((Entry $variant $metrics))))
      (currentContextFrame)))

(= (ctx-set-certified-method $description $parameters $evalProtocol $hash)
   (progn
      (change-state! &ctx-method
         (CertifiedMethod
            (status Certified)
            (description $description)
            (parameters $parameters)
            (evaluation-protocol $evalProtocol)
            (certificate-hash $hash)))
      (currentContextFrame)))

(= (ctx-add-deliverable $artifact)
   (progn
      (change-state! &ctx-deliverables
         (append (get-state &ctx-deliverables)
            ((Artifact $artifact Pending))))
      (currentContextFrame)))