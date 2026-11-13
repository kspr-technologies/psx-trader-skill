-- PSX Autorun: the small app that launchd starts every 5 minutes.
--
-- Why an app at all? macOS guards the Documents folder. It lets a background job in only
-- when the job is an app that you allowed once. This app is that app. It also shows the
-- job's messages as Mac notifications under its own name, "PSX Autorun".
--
-- What it does: it runs run.sh once (the job name comes from launchd), waits for it, and
-- shows each line that starts with "notify<tab>" as a notification:
--   notify<tab>title<tab>text<tab>sound
-- A "\n" inside the text is a new line (the footer goes on a line of its own).
-- A click on a notification, or a double-click on the app, opens the newest report.
--
-- run.sh exit 3: no Python. The app says so ("The tick job did not run: ...").
-- run.sh exit 4: the same, but it was said less than an hour ago. The app shows nothing.
--
-- It never places an order. It only starts run.sh, which starts autorun.py.
-- install.sh puts in the path of run.sh, then builds the app with osacompile.
-- The app keeps the same path inside, so a new Python never means a new app (and a new "Allow").

property launcherPath : "__LAUNCHER__"
property footerText : "A setup, an alert or a signal is a rule that matched. It is not advice. Nothing was bought or sold. Auto-run never places an order."

on run
	set job to system attribute "PSX_JOB"
	if job is "" then set job to "open-latest"
	set cmd to "/bin/sh " & quoted form of launcherPath & " " & quoted form of job
	try
		set out to do shell script cmd
	on error errText number errNum
		if errNum is 4 then return
		if (length of errText) > 150 then set errText to text 1 thru 150 of errText
		display notification ("The " & job & " job did not run: " & errText & linefeed & footerText) with title "PSX auto-run: problem" sound name "Basso"
		return
	end try
	set oldDelims to AppleScript's text item delimiters
	try
		repeat with oneLine in paragraphs of out
			set lineText to oneLine as text
			if lineText starts with ("notify" & tab) then
				set AppleScript's text item delimiters to tab
				set parts to text items of lineText
				if (count of parts) is greater than or equal to 3 then
					set theTitle to item 2 of parts
					set theText to my newLines(item 3 of parts)
					set theSound to ""
					if (count of parts) is greater than or equal to 4 then set theSound to item 4 of parts
					if theSound is "" then
						display notification theText with title theTitle
					else
						display notification theText with title theTitle sound name theSound
					end if
					delay 0.5
				end if
			end if
		end repeat
	end try
	set AppleScript's text item delimiters to oldDelims
end run

on newLines(t)
	set saved to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "\\n"
	set bits to text items of t
	set AppleScript's text item delimiters to linefeed
	set t to bits as text
	set AppleScript's text item delimiters to saved
	return t
end newLines
