Scriptname MantellaListenerScript extends ReferenceAlias
; ---------------------------------------------
; KGTemplates:GivePlayerItemsOnModStart.psc - by kinggath
; ---------------------------------------------
; Reusage Rights ------------------------------
; You are free to use this script or portions of it in your own mods, provided you give me credit in your description and maintain this section of comments in any released source code (which includes the IMPORTED SCRIPT CREDIT section to give credit to anyone in the associated Import scripts below).
; 
; Warning !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
; Do not directly recompile this script for redistribution without first renaming it to avoid compatibility issues with the mod this came from.
; 
; IMPORTED SCRIPT CREDITS
; N/A
; ---------------------------------------------

Import F4SE
Import F4SE_HTTP
Spell property MantellaSpell auto
Actor property PlayerRef auto
Weapon property MantellaGun auto
Holotape property MantellaSettingsHolotape auto
Quest Property MantellaActorList  Auto  
ReferenceAlias Property PotentialActor1  Auto  
ReferenceAlias Property PotentialActor2  Auto
MantellaRepository property repository auto
MantellaConversation property conversation auto
Keyword Property AmmoKeyword Auto Const
GlobalVariable property MantellaRadiantEnabled auto
GlobalVariable property MantellaRadiantDistance auto
GlobalVariable property MantellaRadiantFrequency auto
int RadiantFrequencyTimerID=1
int CleanupconversationTimer=2
int DictionaryCleanTimer=3

Float meterUnits = 78.74
Worldspace PrewarWorldspace
bool itemsGiven
Quest Property MantellaNPCCollectionQuest Auto 
RefCollectionAlias Property MantellaNPCCollection  Auto
Faction Property MantellaFunctionTargetFaction Auto
Message property MantellaTutorialMessage auto

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Initialization events and functions  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

Event OnInit ()
	PrewarWorldspace = Game.GetFormFromFile(0x000A7FF4, "Fallout4.esm") as Worldspace
    LoadMantellaEvents()
	TryToGiveItems()
    
EndEvent

Event OnPlayerTeleport()
    if !itemsGiven
	    TryToGiveItems()
    endif
    If !(conversation.IsRunning())
        Actor[] ActorsInCell = repository.ScanAndReturnNearbyActors(MantellaNPCCollectionQuest, MantellaNPCCollection, false)
        repository.DispelAllMantellaMagicEffectsFromActors(ActorsInCell)
        repository.RemoveFactionFromActors(ActorsInCell,MantellaFunctionTargetFaction)
    endif
EndEvent

Function TryToGiveItems()
	Worldspace PlayerWorldspace = Game.GetPlayer().GetWorldspace()
	if(PlayerWorldspace == PrewarWorldspace || PlayerWorldspace == None)
		;RegisterForPlayerTeleport() ;not nessary to interact with this anymore as it's handled in LoadMantellaEvents()
	else
		;UnregisterForPlayerTeleport()  ;not nessary to interact with this anymore as it's handled in LoadMantellaEvents()
        showAndResolveTutorialMessage()
        repository.doTutorialIntro()
        repository.allowCrosshairTracking = true
		PlayerRef.AddItem(MantellaGun, 1, false)
        PlayerRef.AddItem(MantellaSettingsHolotape, 1, false)
        If !(PlayerRef.HasPerk(repository.ActivatePerk))
            PlayerRef.AddPerk(repository.ActivatePerk, False)
        Endif
        itemsGiven=true
        StartTimer(MantellaRadiantFrequency.getValue(),RadiantFrequencyTimerID)   
	endif
EndFunction



;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Message and tutorial resolution  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

function showAndResolveTutorialMessage()
    int aButton=MantellaTutorialMessage.show()
    if aButton==1 ;player chose no
        repository.TriggerTutorialVariables(false)
        Debug.MessageBox("You can reactivate the tutorial at any time by using the holotape in main settings.")
    elseif aButton==0 ;player chose yes
        repository.TriggerTutorialVariables(true)
        
    endif 
Endfunction




;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Events and functions at player load  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

Event OnPlayerLoadGame()
    LoadMantellaEvents()
    conversation.OnLoadGame()
EndEvent

Function LoadMantellaEvents()
    conversation.SetGameRefs()
    repository.reloadKeys()
    registerForPlayerEvents()
    ;Will clean up all all conversation loops if they're still occuring
    ; repository.endFlagMantellaConversationOne = True    
    StartTimer(3000,DictionaryCleanTimer) 
    If (conversation.IsRunning())   
        Actor[] ActorsInCell = repository.ScanAndReturnNearbyActors(MantellaNPCCollectionQuest, MantellaNPCCollection, false)
        repository.DispelAllMantellaMagicEffectsFromActors(ActorsInCell)
        repository.RemoveFactionFromActors(ActorsInCell,MantellaFunctionTargetFaction)
        conversation.conversationIsEnding=false  ;just here as a safety to prevent locking out the player out of initiating conversations
        conversation.EndConversation();Should there still be a running conversation after a load, end it
        StartTimer(5,CleanupconversationTimer) ;Start a timmer to make second hard reset if conversation is still running after
    EndIf
        Worldspace PlayerWorldspace = PlayerRef.GetWorldspace()
    if(PlayerWorldspace != PrewarWorldspace && PlayerWorldspace != None)
        StartTimer(MantellaRadiantFrequency.getValue(),RadiantFrequencyTimerID)
    endif
    CheckGameVersionForMantella()
Endfunction

Function CheckGameVersionForMantella()
    string MantellaVersion="Mantella.esp 0.13.0"
    if  !IsF4SEProperlyInstalled() 
        debug.messagebox("F4SE not properly installed, Mantella will not work correctly")
    endif
    repository.currentFO4version = Debug.GetVersionNumber()
    Debug.Notification("Version " + repository.currentFO4version)
    repository.isFO4VR = false
    if repository.currentFO4version == "1.10.984.0"
        debug.notification("Currently running "+ MantellaVersion + " NG")
    elseif repository.currentFO4version == "1.10.163.0"
        debug.notification("Currently running "+ MantellaVersion)
    elseif repository.currentFO4version == "1.2.72.0"
        repository.isFO4VR = true
        debug.notification("Currently running "+ MantellaVersion+" VR")
        repository.microphoneEnabled = repository.isFO4VR
    else
        debug.messagebox("The current FO4 version doesn't support Mantella.")
    endif
Endfunction

bool Function IsF4SEProperlyInstalled() 
    int major = F4SE.GetVersion()
    int minor = F4SE.GetVersionMinor()
    int beta = F4SE.GetVersionBeta()
    int release = F4SE.GetVersionRelease()

    return (major != 0 || minor != 0 || beta != 0 || release != 0)
EndFunction

Function registerForPlayerEvents()
        ;resets AddInventoryEventFilter, necessary for OnItemAdded & OnItemRemoved to work properl
        RemoveAllInventoryEventFilters()
        AddInventoryEventFilter(none) 
        ;Register for player sleep events
        RegisterForPlayerSleep()
        ;resets RegisterForHitEvent & RegisterForRadiationDamageEvent at load, necessary for Onhit to work properly
        UnregisterForAllHitEvents()
        RegisterForHitEvent(PlayerRef)
        UnregisterForAllRadiationDamageEvents()
        RegisterForRadiationDamageEvent(PlayerRef)
        RegisterForPlayerTeleport()
Endfunction

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Timer management  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

Event Ontimer( int TimerID)
    if TimerID==RadiantFrequencyTimerID
        if MantellaRadiantEnabled.GetValue()==1.000
            if !conversation.IsRunning()
                MantellaActorList.start()
                Actor Actor1 = PotentialActor1.GetReference() as Actor
                Actor Actor2 = PotentialActor2.GetReference() as Actor

                if (Actor1 && Actor2)
                    ; Filter out non-humanoid actors (cats, dogs, brahmin, etc.)
                    ; ActorTypeNPC keyword = 0x13794 in Fallout4.esm
                    Keyword kActorTypeNPC = Game.GetFormFromFile(0x00013794, "Fallout4.esm") as Keyword
                    if kActorTypeNPC && Actor1.HasKeyword(kActorTypeNPC) && Actor2.HasKeyword(kActorTypeNPC)
                        float distanceToClosestActor = game.getplayer().GetDistance(Actor1)
                    float maxDistance = ConvertMeterToGameUnits(repository.radiantDistance)
                    if distanceToClosestActor <= maxDistance
                        float distanceBetweenActors = Actor1.GetDistance(Actor2)
                        if (distanceBetweenActors <= 1000)
                            MantellaSpell.Cast(Actor2 as ObjectReference, Actor1 as ObjectReference)
                            ; After starting 2-NPC conversation, randomly add 0-3 nearby NPCs
                            Utility.Wait(0.5) ; let conversation initialize
                            if conversation.IsRunning()
                                Actor[] nearby = repository.ScanAndReturnNearbyActors(MantellaNPCCollectionQuest, MantellaNPCCollection, false)
                                ; Shuffle and pick up to 3 extras
                                int extras = Utility.RandomInt(0, 3)
                                int added = 0
                                int attempts = 0
                                while added < extras && attempts < nearby.Length
                                    int pick = Utility.RandomInt(0, nearby.Length - 1)
                                    Actor candidate = nearby[pick]
                                    if candidate && candidate != Actor1 && candidate != Actor2 && candidate != game.getplayer()
                                        float dist = Actor1.GetDistance(candidate)
                                        if dist <= 1000
                                            Actor[] toAdd = new Actor[1]
                                            toAdd[0] = candidate
                                            conversation.AddActorsToConversation(toAdd)
                                            added += 1
                                        endif
                                    endif
                                    attempts += 1
                                endwhile
                            endif
                        endIf
                    endIf
                    endIf
                endIf

                MantellaActorList.stop()
            endIf
        endIf
        StartTimer(MantellaRadiantFrequency.getValue(),RadiantFrequencyTimerID)   
    elseif TimerID==CleanupconversationTimer 
        if conversation.IsRunning() ;attempts to make a hard reset of the conversation if it's still going on for some reason
            ;previous conversation detected, forcing conversation to end.
            debug.notification("Previous conversation detected on load : Cleaning up.")
            Conversation.CleanupConversation()
            conversation.conversationIsEnding = false
        endif
    elseif TimerID==DictionaryCleanTimer 
        if conversation.IsRunning() 
            StartTimer(3000,DictionaryCleanTimer) 
            ;debug.Notification("Can't empty dictionaries because there's an ongoing conversation")
        else
            F4SE_HTTP.clearAllDictionaries() ;This function might lead to crash, monitor if players are reporting crashes
            StartTimer(3000,DictionaryCleanTimer) 
            ;debug.Notification("Emptying dictionaries after a long period of inactivity to prevent memory leaks")
        endif
    endif
EndEvent

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Game event listeners  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

; Disabled: loot/deposit events are noise that derails conversation
Event OnItemAdded(Form akBaseItem, int aiItemCount, ObjectReference akItemReference, ObjectReference akSourceContainer)
EndEvent

Event OnItemRemoved(Form akBaseItem, int aiItemCount, ObjectReference akItemReference, ObjectReference akDestContainer)
endEvent



String lastHitSource = ""
String lastAggressor = ""
Int timesHitSameAggressorSource = 0
Event OnHit(ObjectReference akTarget, ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked, string apMaterial)
    if repository.playerTrackingOnHit && !repository.EventOnHitSpamBlocker && !PlayerRef.IsFlying()
        string aggressor = akAggressor.getdisplayname()
        string hitSource = akSource.getname()

        ; Filter out mod spells and empty aggressors (SleepIntimate, AAF, etc.)
        if aggressor == "" || hitSource == "intimate AAC"
            RegisterForHitEvent(PlayerRef)
            return
        endif

        ; Only report first hit from each aggressor, ignore repeated hits
        if (aggressor != lastAggressor)
            lastAggressor = aggressor
            lastHitSource = hitSource

            if (hitSource == "None") || (hitSource == "")
                conversation.AddIngameEvent(aggressor + " punched the player.")
            else
                if aggressor == PlayerRef.getdisplayname()
                    if playerref.getleveledactorbase().getsex() == 0
                        conversation.AddIngameEvent("The player hit himself with " + hitSource+".")
                    else
                        conversation.AddIngameEvent("The player hit herself with " + hitSource+".")
                    endIf
                else
                    conversation.AddIngameEvent(aggressor + " hit the player with " + hitSource+".")
                endif
            endIf

            repository.HitCount += 1
            if repository.HitCount >= 2
                repository.EventOnHitSpamBlocker = true
                repository.HitCount = 0
            endif
        endIf
    endif
    RegisterForHitEvent(PlayerRef)
EndEvent

Event OnLocationChange(Location akOldLoc, Location akNewLoc)
    ; check if radiant dialogue is playing, and end conversation if the player leaves the area
    If (conversation.IsRunning() && !conversation.IsPlayerInConversation())
        conversation.EndConversation()
    EndIf

    if repository.playerTrackingOnLocationChange
        String currLoc = (akNewLoc as form).getname()
        if currLoc == ""
            currLoc = "Commonwealth"
        endIf
        ;Debug.MessageBox("Current location is now " + currLoc)
        conversation.AddIngameEvent("Current location is now " + currLoc+ ".")
    endif
endEvent

string _lastPlayerEquipEvent = ""

Event OnItemEquipped(Form akBaseObject, ObjectReference akReference)
    if repository.playerTrackingOnObjectEquipped
        string itemEquipped = akBaseObject.getname()
        ; Filter junk: Mantella internals, jewelry, Pip-Boy, misc mod items
        if itemEquipped == "Mantella" || itemEquipped == "Pip-Boy" || itemEquipped == "Wedding Ring"
            return
        endif
        Enchantment ench = akBaseObject.GetEnchantment()
        if ench != None
            string msg = "The player equipped " + itemEquipped + "."
            ; Deduplicate rapid-fire equip events (mod automation)
            if msg != _lastPlayerEquipEvent
                _lastPlayerEquipEvent = msg
                conversation.AddIngameEvent(msg)
            endif
        endif
    endif
endEvent

Event OnItemUnequipped (Form akBaseObject, ObjectReference akReference)
    ; Disabled: unequip events are almost always noise (mod gear swaps, etc.)
endEvent

Event OnSit(ObjectReference akFurniture)
    if repository.playerTrackingOnSit
        ;Debug.MessageBox("The player sat down.")
        String furnitureName = akFurniture.getbaseobject().getname()
        if furnitureName != "Power Armor"
            conversation.AddIngameEvent("The player rested on / used a(n) "+furnitureName+ ".")
        endif
    endif
endEvent


Event OnGetUp(ObjectReference akFurniture)
    if repository.playerTrackingOnGetUp
        ;Debug.MessageBox("The player stood up.")
        String furnitureName = akFurniture.getbaseobject().getname()
        if furnitureName != "Power Armor"
            conversation.AddIngameEvent("The player stood up from a(n) "+furnitureName+ ".")
        endif    
    endif
EndEvent


Event OnDying(Actor akKiller)
    If (conversation.IsRunning())
        conversation.EndConversation()
    EndIf
EndEvent

string lastWeaponFired =""
Event OnPlayerFireWeapon(Form akBaseObject)
    if repository.playerTrackingFireWeapon 
        string weaponName=akBaseObject.getname()
        if weaponName!="Mantella"
            if lastWeaponFired!=akBaseObject && !repository.EventFireWeaponSpamBlocker
                if weaponName!=""
                    conversation.AddIngameEvent("The player used their "+weaponName+" weapon.")
                else
                    conversation.AddIngameEvent("The player used an unarmed attack.")
                endif
                lastWeaponFired=akBaseObject
                repository.WeaponFiredCount+=1
                if repository.WeaponFiredCount>=3
                    repository.EventFireWeaponSpamBlocker=true
                    repository.WeaponFiredCount=0
                endif
            endif    
        endif
    endif
endEvent

Event OnRadiationDamage(ObjectReference akTarget, bool abIngested)
    if repository.playerTrackingRadiationDamage
        if ( abIngested )
            conversation.AddIngameEvent("The player consumed irradiated sustenance.")
        elseif repository.EventRadiationDamageSpamBlocker!=true
            conversation.AddIngameEvent("The player took damage from radiation exposure.")
            repository.EventRadiationDamageSpamBlocker=true
        endif
    endif
    RegisterForRadiationDamageEvent(PlayerRef)
EndEvent

float sleepstartTime
Event OnPlayerSleepStart(float afSleepStartTime, float afDesiredSleepEndTime, ObjectReference akBed)
    sleepstartTime=afSleepStartTime
EndEvent

Event OnPlayerSleepStop(bool abInterrupted, ObjectReference akBed)
    if repository.playerTrackingSleep
        float timeSlept= Utility.GetCurrentGameTime()-sleepstartTime
        string sleepMessage
        string bedName=akBed.getbaseobject().getname()
        string messagePrefix
        if abInterrupted
            messagePrefix="The player's sleep in a "+bedName+" was interrupted after "
        else
            messagePrefix="The player slept in a "+bedName+" for "
        endif
        ;if timeSlept>1
        ;    int daysPassed=Math.floor(timeSlept)
        ;    float remainingDayFraction=(timeSlept- daysPassed)
        ;    int hoursPassed=Math.Floor(remainingDayFraction*24)
        ;    sleepMessage=messagePrefix+daysPassed+" days and "+hoursPassed+" hours."
        ;    SUP_F4SE.WriteStringToFile("_mantella_in_game_events.txt", sleepMessage, 2)
        ;Else
            int hoursPassed=Math.Floor(timeSlept*24)
            sleepMessage=messagePrefix+hoursPassed+" hours."
            conversation.AddIngameEvent(sleepMessage)
        ;endif
    endif
EndEvent

Event OnCripple(ActorValue akActorValue, bool abCrippled)
    if repository.playerTrackingCripple
        string messageSuffix=" is crippled."
        if !abCrippled
            messageSuffix=" is now healed."
        endif
        if akActorValue
            conversation.AddIngameEvent("The player's "+akActorValue.getname()+messageSuffix)
        endif
    endif

EndEvent
Event OnPlayerHealTeammate(Actor akTeammate)
    if repository.playerTrackingHealTeammate
        string messageEvent="The player has healed "+akTeammate.getdisplayname()+"."
        conversation.AddIngameEvent(messageEvent)
    endif
EndEvent

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;   Math functions  ;
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

Float Function ConvertMeterToGameUnits(Float meter)
    Return Meter * meterUnits
EndFunction

Float Function ConvertGameUnitsToMeter(Float gameUnits)
    Return gameUnits / meterUnits
EndFunction