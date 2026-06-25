;configured at runtime:
(= (commchannel)    (empty))
(= (bot_token)      (empty))
(= (channel_id)     (empty))
(= (poll_interval)  (empty))
(= (server_url)     (empty))

;Connect the configured channel — MeTTa resolves config, Python starts it:
(= (initChannels)
   (progn (println! "Initializing channels")
          (configure commchannel    irc)
          (configure bot_token      "")
          (configure channel_id     "") ; channel_id and chat_id can be used interchangably, depending on the channel type
          (configure poll_interval  20)
          (configure server_url     "")
          (py-call (channels_registry.start
                      (commchannel)
                      (bot_token)
                      (channel_id)
                      (poll_interval)
                      (server_url)))))

;Receive the latest user message:
(= (receive)
   (py-call (channels_registry.getLastMessage)))

;Send a message:
!(change-state! &lastsend "")
(= (send $msg)
   (if (!= $msg (get-state &lastsend))
       (progn (change-state! &lastsend $msg)
              (let $safemsg (string-replace $msg "\n" "\\n")
                   (py-call (channels_registry.send_message $safemsg))))
       _))

;Search the internet for some information:
(= (search $msg)
   (py-call (websearch.search $msg)))