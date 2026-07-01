; This file is MeTTa trampoline to the plugin.py file

!(import! &self (library OmegaClaw-Core ./src/plugin.py))

(= (initPlugins)
   (py-call (plugin.initPlugins)))

(= (commChannelConfig $commchannel)
   (py-call (plugin.commChannelConfig $commchannel (py-call (plugin.commandLineToDict (command-line))))))

(= (commChannelReceive)
   (py-call (plugin.commChannelReceive)))

(= (commChannelSend $message)
   (py-call (plugin.commChannelSend $message)))

(= (llmProviderConfig $provider)
   (py-call (plugin.llmProviderConfig $provider (py-call (plugin.commandLineToDict (command-line))))))

(= (llmProviderChat $prompt $max_tokens $reasoning_mode)
   (py-call (plugin.llmProviderChat $prompt $max_tokens $reasoning_mode)))
