**Check**

  Does DubEditor have trouble with dubs that last to the end or is the "lasts to the end" setting not accurate? Might have to implement a buffer again. It is possible that Dub extends to the end doesn't do anything in certain cases.


**Short term**


  test: allow cuda download when user enables acceleration in settings

  test: ffmpeg installer if ffmpeg not on path
 
    Check with Agent: if we're switching to c++ we have to do a lot of cleaning up

  Check with Agent: Readme feedback and markup tips

  Check and rename menu items

**Mid term**


  test ffmpeg installer if ffmpeg not on path

  Create a builder script for GF and FR

  Make GF and FR available standalone
  
  Cuda packaging doesn't work

  Add the option to open DubEditor from AD (define install directory)

  Loading Whisper models cannot be cancelled and the call seems to fail when switching while a model is still loading

  New tool to manage packs: Move everything in the Dub folder into a subfolder named after the pack (user input). Generate a list of Packs. Make steps reversible. Warn when restoring a pack but there are already video/srt files present (give the option to merge the present files with the loaded pack). Give the option to add certain files to a pack. Give the option to add an image as thumbnail. PackMan. Next step after PackMan would be GenderFixer to undo Dub Editor fuck-ups and find missing speakers
  
  add the ability to open playback directly from AutoDub (preview already exists, but it was designed for a different purpose, and poorly)

  can cuda, cpu and models be installed on path as well? If so, implement a check in the download workflows


**Long term**
  check if models have been updated and set latest download
  reimplement stripping unnecessary dependencies for filesize eventually