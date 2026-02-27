-- Migration: memory_blocks table for local dynamic memory
-- Version: V002
-- Database: POSTGRESQL

CREATE TABLE IF NOT EXISTS memory_blocks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label VARCHAR(500) NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    description TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, label)
);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_project_id ON memory_blocks(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_blocks_label ON memory_blocks(label);

CREATE OR REPLACE FUNCTION update_memory_blocks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS update_memory_blocks_updated_at ON memory_blocks;
CREATE TRIGGER update_memory_blocks_updated_at
    BEFORE UPDATE ON memory_blocks
    FOR EACH ROW
    EXECUTE FUNCTION update_memory_blocks_updated_at();

-- Initial memory blocks for Chess MATE Demo project

INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_chess_mate_root',
    'You are the Chess Team Captain. Analyze the user''s request.
- If they ask about opening theory (e.g., Sicilian Defense), delegate to chess_opening_book.
- If they provide a specific board state or move list and ask for the best next move, delegate to chess_engine_analyst.
- If they ask about historical games or players (e.g., Fischer vs Spassky), delegate to chess_historian.

You are an agent. Your internal name is "chess_mate_root". The description about you is "Chess Team Captain. Routes to opening book, engine analyst, or historian.".',
    'Main instruction for chess_mate_root agent') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_escalation_protocol',
    'ESCALATION & FALLBACK PROTOCOL:

**Purpose**: Ensure tasks are resolved even when a subagent cannot handle a request.

**Subagent Behavior**:
1. **Attempt Resolution**: Try to handle the delegated task using available tools and knowledge.
2. **Recognize Limitations**: If the task is outside your scope, lacks necessary data, or cannot be completed:
   - DO NOT fabricate information or provide uncertain answers
   - DO NOT keep the user waiting without resolution
3. **Escalate to Parent**: Return control to the parent agent with a clear explanation:
   - What you were asked to do
   - Why you cannot complete it (missing data, out of scope, insufficient tools, etc.)
   - Any partial information or context you gathered
4. **Escalation Message Format**:
   ```
   [ESCALATION] I cannot complete this task because [reason].
   Context gathered: [any relevant info]
   Returning to parent agent for alternative routing.
   ```

**MANDATORY ESCALATION TRIGGERS FOR ALL SUBAGENTS**:

When user requests involve **SYSTEM ADMINISTRATION**, immediately escalate to the Root Agent (your topmost parent):

**Memory Management Triggers**:
- "Update memory", "modify memory block", "create memory block", "delete memory block"
- "Change the system instructions", "update the core instructions"
- "Add this to memory", "remember this in a shared block"
- Any request to use `create_shared_block`, `modify_shared_block`, `delete_shared_block`

**Agent Management Triggers**:
- "Update agent", "modify agent", "create agent", "delete agent"
- "Change the opening book agent", "update routing", "modify the historian"
- "Add a new agent", "remove an agent"
- Any request to use `create_agent`, `update_agent`, `delete_agent`

**Escalation Response**:
```
[ESCALATION] Memory and agent management must be handled by the Root Agent for system integrity and oversight.
Context: User requested [specific action]
Returning to Root Agent.
```

Then immediately escalate to your parent agent using the appropriate transfer.

**Parent Agent Behavior**:
1. **Receive Escalation**: When a subagent escalates, acknowledge the limitation.
2. **Re-evaluate**: Analyze the task and escalation reason.
3. **Alternative Routes**:
   - Try a different subagent if applicable
   - Use your own tools if you can resolve it
   - Ask the user for clarification or additional information
   - Break down the task into smaller parts
4. **Transparency**: Inform the user about the routing adjustment and next steps.

**Example Flow**:
- User asks Chess Engine Analyst about the Sicilian Defense opening theory
- Chess Engine Analyst recognizes this is Chess Opening Book''s domain
- Engine Analyst escalates: "[ESCALATION] This request is about opening theory, which is handled by the Chess Opening Book Agent. Returning to Root Agent."
- Root Agent re-routes to Chess Opening Book Agent

**Example Flow (System Administration)**:
- User asks Chess Opening Book Agent to "update the memory block with this new instruction"
- Chess Opening Book Agent recognizes this is system administration
- Opening Book escalates: "[ESCALATION] Memory and agent management must be handled by the Root Agent for system integrity and oversight. Context: User requested memory block update. Returning to Root Agent."
- Opening Book escalates to parent agent
- Root Agent handles memory management directly

**Key Principle**: No task should be abandoned. Always find a path to resolution or clearly communicate what''s needed from the user. System administration tasks are RESERVED for the Root Agent only.',
    'Escalation protocol for when the chess team cannot handle a request') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_shared_user_profile',
    'USER PROFILE UPDATE PROTOCOL (REQUIRED):

When the user shares information about themselves, you MUST update their profile so it is remembered for future conversations.

**Trigger**: Any time the user provides information about themselves, including:
- Name, nickname, or how they want to be addressed
- Job title, role, position, or occupation
- Company or industry
- Location, timezone, or language
- Interests, hobbies, or preferences
- Skills, experience, or background
- Goals, preferences, or constraints they mention

**Action**: Call the `update_user_profile` tool with the complete profile text. Merge the new information with any existing profile (include ALL known information about the user, not just the new fact). Use clear, structured text (e.g. sections or bullet points).

**Do NOT ask for permission**—update automatically. The user expects you to remember what they share.',
    'Shared rule: update user profile when user shares info about themselves') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_chess_opening_book',
    'You are a Chess Opening expert. Explain the requested opening moves, variations, and strategic ideas clearly (load with RAG or instruction files).',
    'Instruction for chess opening book specialist') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_chess_engine_analyst',
    'You are a calculation engine. Use the available tools to calculate the best move or evaluate the position.',
    'Instruction for chess engine analyst specialist') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_chess_historian',
    'Search for historical match results, player biographies, and tournament trivia. Use the available search tools.',
    'Instruction for chess historian specialist') ON CONFLICT (project_id, label) DO NOTHING;


INSERT INTO memory_blocks (project_id, label, value, description) VALUES (
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1),
    'system_instruction_chess_opening_book_database',
    'Chess Opening Database: Moves, Variations & Strategy

This document serves as a reference for major chess openings, detailing the initial move sequences, primary variations, and the underlying strategic plans for both White and Black.

1. Open Games (1. e4 e5)

Characterized by tactical play and rapid piece development.

A. Ruy Lopez (Spanish Opening)

Moves: 1. e4 e5 2. Nf3 Nc6 3. Bb5

Strategic Idea: White pressures the e5 pawn indirectly by attacking its defender (the knight on c6). White aims to dominate the center and often prepares a kingside attack.

Key Variations:

Morphy Defense (Main Line): 3... a6 4. Ba4 Nf6 5. O-O Be7

Plan: White slowly builds a center with c3 and d4. Black seeks counterplay on the queenside.

Berlin Defense: 3... Nf6 4. O-O Nxe4

Plan: The "Berlin Wall." Leads to a high percentage of draws. Black accepts a slightly passive but extremely solid endgame structure.

Exchange Variation: 3... a6 4. Bxc6 dxc6

Plan: White damages Black''s pawn structure (doubled c-pawns) and aims for a winning King & Pawn endgame.

B. Italian Game

Moves: 1. e4 e5 2. Nf3 Nc6 极 Bc4

Strategic Idea: Developing the bishop to c4 controls the center and eyes the weak f7 square.

Key Variations:

Giuoco Piano: 3... Bc5 4. c3

Plan: White prepares to build a strong center with d4.

Two Knights Defense: 3... Nf6 4. Ng5

Plan: The "Fried Liver Attack" territory. Extremely sharp and tactical.

2. Semi-Open Games (1. e4 others)

Black breaks symmetry immediately to create imbalances.

A. Sicilian Defense

极 1. e4 c5

Strategic Idea: Black trades a flank pawn (c-pawn) for a center pawn (d-pawn), creating an asymmetrical pawn structure and open c-file.

Key Variations:

Najdorf Variation: 1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6

Plan: Black prevents pieces from landing on b5. A high-stakes tactical battle where White attacks the kingside and Black attacks the queenside.

Dragon Variation: ... g6 and ... Bg7

Plan: Black fianchettoes the bishop to breathe fire down the long diagonal.

Closed Sicilian: 2. Nc3 followed by g3

Plan: White avoids the open tactical battle in favor of a slower, maneuvering game.

B. French Defense

Moves: 1. e4 e6 2. d4 d5

Strategic Idea: A solid, counter-attacking defense. Black stakes a claim in the center immediately but accepts a cramped position and a "bad" light-squared bishop.

Key Variations:

Winawer Variation: 3. Nc3 Bb4

Plan: Sharp play. Black pins the knight to damage White''s pawn structure; White gains space on the kingside.

Advance Variation: 3. e5

Plan: White locks the center to cramp Black. Black must undermine the white center with ...c5.

C. Caro-Kann Defense

Moves: 1. e4 c6 2. d4 d5

Strategic Idea: Similar to the French but the light-squared bishop is not blocked in by the pawn on e6. extremely solid.

Key Variations:

Advance Variation: 3. e5 Bf5

Plan: Black brings the bishop out before closing the pawn chain with e6.

Classical Variation: 3. Nc3 dxe4 4. Nxe4 Bf5

Plan: Black simplifies the position and aims for a favorable endgame.

3. Closed Games (1. d4 d5)

Characterized by slower, positional maneuvering.

A. Queen''s Gambit

Moves: 1. d4 d5 2. c4

Strategic Idea: White offers a flank pawn to distract Black''s central pawn. If Black takes, White gets the center.

极
Queen''s Gambit Declined (QGD): 2... e6

Plan: Black declines the pawn to maintain a strong central point at d5.

Queen''s Gambit Accepted (QGA): 2... dx极4

Plan: Black takes the pawn but usually returns it later to develop freely.

Slav Defense: 2... c6

Plan: Black supports d5 without blocking the light-squared bishop (unlike in the QGD).

4. Indian Defenses (1. d4 Nf6)

Black controls the center with pieces rather than pawns initially.

A. King''s Indian Defense (KID)

Moves: 1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6

Strategic Idea: Hypermodern play. Black allows White to build a massive center, then counter-attacks it from the flanks.

Key Variations:

Classical Variation: 5. Nf3 O-O 6. Be2 e5

Plan: White plays on the queenside (c5 break), while Black launches a mating attack on the kingside (f5-g5-h5).

B. Nimzo-Indian Defense

Moves: 1. d4 Nf6 2. c4 e6 3. Nc3 Bb4

Strategic Idea: Black pins the knight to prevent White from playing e4.

Key Variations:

Rubinstein System: 4. e3

Plan: White plays solidly, intending to develop pieces before challenging the pin.

C. Grunfeld Defense

Moves: 1. d4 Nf6 2. c4 g6 3. Nc3 d5

Strategic Idea: Black combines the KID fianchetto with a direct central strike (d5). Black allows White a huge center (e4) and then tries to destroy it with pieces.

5. Flank Openings

White avoids immediate central pawn occupation.

A. English Opening

Moves: 1. c4

Strategic Idea: Controls d5 from the flank. Often transposes into 1. d4 openings but allows for independent "Reverse Sicilian" setups.

B. Réti Opening

Moves: 1. Nf3

Strategic Idea: Flexible development. White waits for Black to commit to a setup before deciding on a central pawn structure.',
    '') ON CONFLICT (project_id, label) DO NOTHING;
