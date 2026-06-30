!(import! &self (library OmegaClaw-Core ./src/plugin.py))

(= (initPlugins)
   (py-call (plugin.initPlugins)))

(= (commChannelConfig $commchannel)
   (py-call (plugin.commChannelConfig $commchannel (py-call (plugin.listToDict (subconfig $commchannel))))))

(= (commChannelReceive)
   (py-call (plugin.commChannelReceive)))

(= (commChannelSend $message)
   (py-call (plugin.commChannelSend $message)))
