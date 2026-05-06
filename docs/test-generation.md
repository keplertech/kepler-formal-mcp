# Using Kepler Formal MCP for Design Comparison

This guide explains how to use the Kepler Formal MCP to analyze and compare Verilog designs with their libraries.

## Overview

The [kepler-formal-regress](https://github.com/keplertech/kepler-formal-regress.git) repository contains ready-to-use design examples with their libraries:
- **black_parrot**: A complete processor design with all necessary files
- **tinyrocket**: A simple RISC-V processor design with all necessary files

Each directory contains `.v` (Verilog) files and `.lib` (library) files that you can use immediately.

## Step 1: Clone the Kepler Formal Regress Repository

```bash
git clone https://github.com/keplertech/kepler-formal-regress.git <path-to-regress>
```

## Step 2: Copy Design Folder to Your Shared Folder

Copy an entire design directory (black_parrot or tinyrocket) to your shared folder:

```bash
# Choose one design
cp -r <path-to-regress>/black_parrot <path-to-shared-folder>/
# or
cp -r <path-to-regress>/tinyrocket <path-to-shared-folder>/
```

Your shared folder now contains all Verilog files and libraries needed for analysis.

## Step 3: Generate Design Variants 

Each design folder may contain a Python script that generates modified versions of the `.v` files (with intentional changes for testing):

```bash
cd <path-to-shared-folder>/black_parrot
python3 black_parrot_edit.py
```

This creates modified versions of the design files that you can compare with the originals.

## Step 4: Use Kepler Formal MCP in Claude

After setting up Claude and configuring the shared folder path in Claude, you can ask it to compare the two versions using the Kepler Formal MCP tools:

### Example: Compare Two Versions
"Use the Kepler Formal MCP tools to compare `tinyrocket.v` with `tinyrocket_modified.v` and identify all differences"

or

"Load both versions of black_parrot with their libraries and perform a formal verification comparison to find the differences"

**Important:** Claude should use the MCP tools directly to analyze the files. Do not ask Claude to read files manually - let the MCP handle the analysis.

The Kepler Formal MCP will:
- Read both `.v` files from your shared folder
- Load the corresponding `.lib` library files
- Perform formal verification analysis
- Report all logic differences between the two versions
