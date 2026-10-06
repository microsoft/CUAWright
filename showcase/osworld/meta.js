// Ten OSWorld-V2 trajectories from CUAWright with GPT-5.5 (reasoning: xhigh).
// Step numbers refer to the model calls in each trace.
window.TRACE_META = [
  {
    id: "030", why: ["Runs real experiments instead of guessing: it scales training from 200 to 7,000 iterations and tests each checkpoint.", "Stops only when the test score meets the target (92.8% against 92%), not when training finishes.", "Checks its work: it compares checkpoint hashes and reads the note table back after writing it."], title: "Train a model until it hits the target", apps: ["Terminal", "PyTorch", "Word"],
    summary: "The agent rebuilds the repo baseline, converts the new dataset into the repo's format, and trains longer and longer runs (0% → 55% → 91% → 92.8% test accuracy) until the model meets the target. Then it fills in the meeting-note table.",
    keys: [
      [4, "Reads the Week 2 TODO in the meeting note"],
      [10, "Sets up the repo environment with uv"],
      [19, "Runs the README Quick Start baseline"],
      [21, "Converts the new dataset to the repo format"],
      [23, "First test of the small model: 0% correct"],
      [28, "1,000 iterations: 55.5%"],
      [30, "5,000 iterations: 90.9%"],
      [33, "7,000 iterations: 92.8%, target met"],
      [35, "Copies the target checkpoint and checks its hash"],
      [36, "Fills in the note table and reads it back"]
    ]
  },
  {
    id: "100", why: ["Follows the rule against DevTools and CDP: it sees the page only through screenshots and moves pieces only with the mouse.", "Turns a visual puzzle into a search problem: it scores how well each pair of piece edges fits and searches for the best layout.", "Builds a small tool for itself: a slowdrag helper (step 23) that it uses for 16 drags after finding that fast drags don't register."], title: "Solve a jigsaw puzzle without DevTools", apps: ["Chrome", "xdotool", "Pillow"],
    summary: "The task forbids DevTools and CDP. So the agent cuts all 16 pieces out of screenshots, searches for the layout whose edges fit together best, and drags each piece into place with the mouse until the page says Completed.",
    keys: [
      [5, "First look: 16 shuffled pieces"],
      [10, "Labels each piece on a screenshot"],
      [17, "Finds that drags need gradual mouse moves"],
      [23, "Writes a reusable slowdrag helper"],
      [80, "Searches for the layout whose edges match best"],
      [83, "Renders the solved layout before moving anything"],
      [134, "Starts placing pieces by row and column"],
      [158, "Puzzle shows Completed ✓"]
    ]
  },
  {
    id: "061", why: ["Treats the example pair as training data and measures the edit rather than guessing it by eye.", "Compares several candidate methods with numbers and side-by-side images before committing to one.", "Writes reusable scripts (test_transforms.py, apply_style.py) and runs the final one once, at full resolution."], title: "Copy a photo edit from one example", apps: ["Python", "Pillow", "NumPy"],
    summary: "The agent uses the before/after pair as training data. It measures the tone and colour shift, renders several transfer methods side by side, picks the closest match, and applies it to the new 48-megapixel photo.",
    keys: [
      [3, "Contact sheet of the three photos"],
      [5, "Measures the colour and brightness shift"],
      [13, "Writes a script that tests several transfer methods"],
      [15, "Compares the candidates side by side"],
      [16, "Scores each candidate against the edited reference"],
      [23, "Applies the chosen method at full resolution"],
      [25, "Checks the final result next to the reference"]
    ]
  },
  {
    id: "066", why: ["Edits the PPTX's XML directly, but checks every change by rendering the slides and looking at them.", "Takes the title colour from the logo's pixels instead of eyeballing it.", "Catches a problem it introduced (the title box is too short), fixes it, and renders again."], title: "Fix slide layout by editing the PPTX", apps: ["PowerPoint XML", "LibreOffice", "Pillow"],
    summary: "The agent reads the slide XML, renders the deck to images to see the problems, takes the title colour from the logo's pixels, moves the shapes in the XML, and renders the deck again after each change.",
    keys: [
      [6, "Reads the shapes and positions in the slide XML"],
      [9, "Renders slide 1 to see the problems"],
      [12, "Takes the colour of the logo's “A” from pixels"],
      [19, "Moves the shapes and recolours the title (keeps a backup)"],
      [21, "Renders again: the title box is too short"],
      [23, "Makes the title box taller"],
      [25, "Final render of slide 1"],
      [26, "Reads back geometry and colours from the saved file"]
    ]
  },
  {
    id: "027", why: ["Very efficient: 16 steps from start to submit, mostly in a few long scripts.", "Recovers from a failed data source (HTTP 429) without getting stuck.", "Reads every metric's definition carefully and reads the written cells back to confirm."], title: "Fill stock workbooks from market data", apps: ["Python", "openpyxl", "HTTP"],
    summary: "In 16 steps, the agent downloads daily prices for five stocks, works around a rate limit, computes all eleven summary metrics in one script, writes both workbooks, and reads the cells back.",
    keys: [
      [2, "Inspects both workbook layouts"],
      [3, "First data request is rate-limited (HTTP 429)"],
      [5, "Downloads 122 trading days for each stock"],
      [10, "Computes metrics for the 21-day window"],
      [13, "Writes daily.xlsx and summary.xlsx in one script"],
      [15, "Reads the saved cells back"]
    ]
  },
  {
    id: "012", why: ["Mixes text search (pdftotext over 61 PDFs) with visual matching (contact sheets) for scanned papers.", "Searches beyond the obvious folders: when one paper is missing, it checks the Trash and finds it there.", "Confirms every file's name and contents before submitting."], title: "Match exam mistakes to past papers", apps: ["Word", "PDF tools", "Files"],
    summary: "The agent pulls the question screenshots out of the .docx, searches the text of 61 PDFs, turns scanned papers into contact sheets to match them by eye, and finds the last missing paper in the Trash.",
    keys: [
      [4, "Extracts the question images from the .docx"],
      [5, "Reads one of the wrong questions"],
      [10, "Extracts text from all 61 PDFs"],
      [20, "Builds contact sheets of scanned papers"],
      [21, "Compares pages visually"],
      [42, "Copies the matches for Q2–Q4"],
      [52, "Q5 is still missing, so it checks the Trash"],
      [58, "Finds the Q5 paper among the deleted files"],
      [60, "Restores it as Q5_13Dec.pdf"],
      [64, "Checks the names and contents of all five files"]
    ]
  },
  {
    id: "104", why: ["Reads exact dimensions from the drawing's vector data instead of estimating them from an image.", "Builds the CAD part with code: a revolve script, a verify script, and a render script.", "Confirms the exported part is one valid solid and renders it to compare against the drawing."], title: "Model a stepped shaft in FreeCAD", apps: ["FreeCAD", "PDF → SVG", "Python"],
    summary: "Rather than estimate sizes from pixels, the agent converts the PDF drawing to SVG and reads exact dimensions from the vector paths. Then it writes a FreeCAD revolve script, checks the exported STEP solid, and renders it for a visual check.",
    keys: [
      [4, "Reads the engineering drawing"],
      [5, "Looks at the reference photo"],
      [21, "Converts the PDF to SVG to get the vector paths"],
      [22, "Finds the position of each dimension label"],
      [35, "Plots the shaft profile from the vector paths"],
      [38, "Tests a revolve in FreeCAD"],
      [58, "Writes build_shaft.py and exports STEP"],
      [59, "Checks the solid: one valid body, 152 mm long"],
      [61, "Renders the finished shaft"]
    ]
  },
  {
    id: "088", why: ["Builds its own tools: Python helpers that the spreadsheet's macro buttons call, kept in Documents so the system keeps working later.", "Diagnoses its own failure: when the Basic loop stops after one contract, it inspects the output and moves that work into a second helper.", "Runs both buttons from start to finish (20 contracts, 20 drafts), then checks the open spreadsheet on screen."], title: "Build a contract and email generator", apps: ["LibreOffice Calc", "Basic macros", "Thunderbird"],
    summary: "The agent builds a spreadsheet with two macro buttons through the UNO API. When the Basic loop fails after the first contract, it moves that work into reusable Python helpers, then runs both buttons to produce 20 contracts and 20 Thunderbird drafts.",
    keys: [
      [2, "Reads the 20 company records"],
      [7, "Lists the placeholders in the template"],
      [24, "Writes a reusable email-draft helper"],
      [27, "Builds the .ods with macros and buttons through UNO"],
      [32, "Test run: generation stops after one contract"],
      [34, "Inspects the one contract that was produced"],
      [36, "Moves contract generation into a Python helper"],
      [38, "Runs both buttons: 20 contracts and 20 drafts"],
      [46, "Checks the buttons in the open spreadsheet"]
    ]
  },
  {
    id: "001", why: ["Skips slow mail and calendar clicking: it reads the mailbox and the calendar's data files directly.", "Applies the user's rule (thesis defenses take priority) by finding exactly which personal events conflict.", "Cleans up its temporary files and confirms the result in the real calendar app."], title: "Schedule thesis defenses from an email", apps: ["Thunderbird", "Excel attachment", "GNOME Calendar"],
    summary: "The agent reads the Thunderbird mailbox directly and extracts the attached schedule. It finds the 7 defenses on Leslie Adams's panels, removes 2 conflicting personal events, and checks the result in GNOME Calendar.",
    keys: [
      [4, "Reads the mailbox with Python"],
      [7, "Saves the schedule spreadsheet attachment"],
      [15, "Finds every defense with Leslie Adams on the panel"],
      [17, "Removes 2 conflicts and adds 7 defenses"],
      [21, "Deletes its temporary files"],
      [34, "Checks the events in GNOME Calendar"]
    ]
  },
  {
    id: "105", why: ["Uses Slicer's own Python to render overlay montages, so it can review a 3D scan as images.", "Reviews many slices over many rounds before changing anything.", "Saves the correction as a script, then compares the original and corrected masks slice by slice."], title: "Check and correct a tumor segmentation", apps: ["3D Slicer", "SimpleITK", "NumPy"],
    summary: "Using 3D Slicer's Python, the agent renders overlay montages of all four MRI scans, reviews them slice by slice, writes a correction script that keeps the BraTS labels, and compares the original and corrected masks before exporting.",
    keys: [
      [7, "Renders overlay montages of all four scans"],
      [8, "Reviews the AI segmentation slice by slice"],
      [50, "Measures image intensity inside each label"],
      [95, "Writes the correction script, keeping BraTS labels"],
      [97, "Renders montages of the saved file"],
      [99, "Compares original and corrected masks"]
    ]
  }
];
