;configured at runtime:
(= (commchannel) (empty))

;Connect all the channels:
(= (initChannels)
   (progn (log INFO "channels" "Initializing channels")
          (configure commchannel irc)
          (commChannelConfig (commchannel))))

;Receive the latest user message considering all communication channels:
(= (receive)
   (commChannelReceive))

;Send a message to all communication channels:
!(change-state! &lastsend "")
(= (send $msg)
   (if (!= $msg (get-state &lastsend))
       (progn (change-state! &lastsend $msg)
              (let $safemsg (string-replace $msg  "\n" "\\n")
                   (let $temp (cut) (commChannelSend $safemsg)))) _))
