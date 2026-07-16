# Recovery inventory — the manually recovered corpus mapped onto SIMANTW.SYM

*Generated 2026-07-16 (session cont.220). Companion machine-readable map:
[`simant/facts/recovered_map.json`](../simant/facts/recovered_map.json) — the
explicit record the DOS_RE 2.0 adapter-routing stage consumes; the pipeline
must never re-parse the Python docstrings this was derived from.*

**Purpose.** The project is adopting the script-driven DOS_RE 2.0 recovery
pipeline. Owner directive: where a verified manual CPU-less implementation
exists in `simant/recovered/`, it is the AUTHORITATIVE implementation — the
generated VM-less layer routes through CPU/ABI adapters around it instead of
emitting a parallel literal lift. This document inventories that corpus,
matches every function to its `SIMANTW.SYM` entry, and records the contracts
the generated adapters need.

## 1. Executive summary

| quantity | count |
|---|---|
| SIMANTW.SYM entries, total | 2171 |
| — code entries (seg1–7, the "1319") | 1319 |
| — data entries (SIMANT_DATA_GROUP / PACK / DGROUP, seg8–10) | 852 |
| — code addresses carrying TWO alias names (CRT aliases, all seg4) | 6 |
| Recovered Python functions (`simant/recovered/`, 9 modules) | 357 |
| Matched SYM code entries (migration table below) | 309 |
| — distinct Python impls behind them | 300 |
| — confidence `exact` / `derived` (twin families) | 297 / 12 |
| — status `proven` | 291 |
| — status `proven-gated` (gates: `_YellowFight`×16, `_DoTroph`×5, `_GetRedBestDirs`×1) | 18 |
| — status `unverified` | 0 |
| Helper-split functions (private pieces of a parent's recovery) | 53 |
| Utility helpers (pervasive idioms: `_sx16`, `crt_math._sx32`) | 2 |
| API wrappers (`lzss.decompress`) | 1 |
| Dead code (`gameplay._sx32` — never called) | 1 |
| Islands in `hooks.py` (distinct install addresses) | 68 |
| — with a pure recovered twin they already route through | 61 |
| — island-only (logic lives in the island body; see §6) | 7 |
| SYM code entries with NO recovery — the lift-don't-recover set | 1009 |

Per-segment coverage (matched / unmatched / total code symbols):

| NE seg | original module | matched | unmatched | total |
|---|---|---|---|---|
| seg1 | SIMANT (UI/menus/game shell) | 0 | 165 | 165 |
| seg2 | GR (graphics device layer) | 0 | 181 | 181 |
| seg3 | ANTEDIT (editor + balloons UI) | 0 | 145 | 145 |
| seg4 | _TEXT (CRT + low-level blitters) | 25 | 228 | 254 |
| seg5 | SIMONE (sim core: map/life/ant primitives) | 112 | 57 | 169 |
| seg6 | SIMANT1 (ant behavior ticks) | 104 | 19 | 123 |
| seg7 | SIMTWO (strategy/worldgen/window) | 68 | 214 | 282 |

*(seg4: 25 matched addresses cover 26 SYM names — the `__aFlmul`/`__aFulmul` alias pair shares one address; unmatched counts are names.)*

The recovery frontier is exactly where the journal says it is: the simulation
core. **seg6 (SIMANT1, the ant-behavior tick layer) is 104/123 recovered** —
only 19 symbols remain (§5a), among them the gate targets. seg5 (SIMONE) is
112/169. seg1/2/3 (game shell, GDI graphics layer, editor UI) are 0% recovered
by deliberate policy ("pivot to gameplay core") — that whole band plus most of
seg4/seg7's presentation half is the lift-don't-recover set.

Verification channels over the 309 matched entries: 254 are covered by the
state-diff oracle (`test_state_diff.py`), 61 by an island A/B oracle
(`test_hooks.py` runs the ORIGINAL ASM and the island over identical inputs),
51 by return-value oracles in `test_hooks.py`, plus `test_lzss.py` /
`test_native.py` / `test_state_view.py` / `capture_ab.py` for the LZSS core and
the bridge seam. Demo-replay tiers (`scripts/verifyislands.py`,
`scripts/liftverify.py`, `scripts/checkpoints.py`) verify the same
correspondence over live gameplay traces.

## 2. Migration table

One row per matched SYM code entry. `confidence`: `exact` = docstring-cited
address resolves in SIMANTW.SYM to exactly the cited symbol; `derived` = a twin
family (one Python impl covering byte-identical compiled copies — the address
and symbol still resolve exactly; the one-to-many mapping is the derivation).
`ret ?` = the docstring does not state the return convention; §4 explains how
the adapter stage closes that hole. Status `proven-gated [X]` = proven by
oracle, but raises `NotImplementedError` on branches that call the unrecovered
`X` (§5).

| IR entry | SYM name | recovered impl | confidence | verification | authority | ret | notes |
|---|---|---|---|---|---|---|---|
| `4:06F6` | `_srand` | `crt_math.c_srand` | exact | proven | authoritative | far |  |
| `4:070A` | `_rand` | `crt_math.c_rand` | exact | proven | authoritative | far |  |
| `4:08D4` | `__aFldiv` | `crt_math.a_f_ldiv` | exact | proven | authoritative | far |  |
| `4:096E` | `__aFulmul` | `crt_math.a_f_ulmul` | exact | proven | authoritative | far | SYM lists alias __aFlmul at the same address (signed/unsigned long mul share code); this impl covers both |
| `4:442C` | `_WindowsMono_MakeTable4x4a` | `render.windows_mono_make_table_4x4` | derived | proven | authoritative | ? | twins: 4:44B9 |
| `4:44B9` | `_WindowsMono_MakeTable4x4b` | `render.windows_mono_make_table_4x4` | derived | proven | authoritative | ? | twins: 4:442C |
| `4:4542` | `_WindowsMono_MakeTable2x2a` | `render.windows_mono_make_table_2x2` | derived | proven | authoritative | ? | twins: 4:45DB |
| `4:45DB` | `_WindowsMono_MakeTable2x2b` | `render.windows_mono_make_table_2x2` | derived | proven | authoritative | ? | twins: 4:4542 |
| `4:4674` | `_Windows_MakeTable4x4` | `render.windows_make_table_4x4` | exact | proven | authoritative | ? |  |
| `4:46BB` | `_Windows_MakeTable1x1` | `render.windows_make_table_1x1` | exact | proven | authoritative | ? |  |
| `4:46E9` | `_GenOverMap` | `render.gen_over_map` | exact | proven | authoritative | ? |  |
| `4:4754` | `_GenNestMap` | `render.gen_nest_map_cells` | exact | proven | authoritative | ? |  |
| `4:47DD` | `_XferTileColor` | `render.xfer_tile_color` | exact | proven | authoritative | ? |  |
| `4:486C` | `_XferTileMono` | `render.xfer_tile_mono` | exact | proven | authoritative | ? |  |
| `4:48FA` | `_XferLifeTileColor` | `render.xfer_life_tile_color` | exact | proven | authoritative | ? |  |
| `4:49B7` | `_XferLifeTileMono` | `render.xfer_life_tile_mono` | exact | proven | authoritative | ? |  |
| `4:4A6B` | `_DoCalcTile` | `render.do_calc_tile` | exact | proven | authoritative | ? |  |
| `4:6C62` | `_CopyChar` | `render.copy_char` | exact | proven | authoritative | ? |  |
| `4:6CAA` | `_CopyCharRep` | `render.copy_char_rep` | exact | proven | authoritative | ? |  |
| `4:6CF8` | `_MoveTextToBalloon` | `render.move_text_to_balloon` | exact | proven | authoritative | ? |  |
| `4:6E05` | `_exchange` | `byteops.exchange` | exact | proven | authoritative | ? |  |
| `4:6E24` | `_os_ClipLine` | `geometry.clip_line` | exact | proven | authoritative | ? | register calling convention: P0 in SI/DI, P1 in DX/BX, bounds in DGROUP 0x1D7A/0x1D78, near call; not stack-args |
| `4:7356` | `_FlipWord` | `byteops.flip_word` | exact | proven | authoritative | ? |  |
| `4:7360` | `_FlipLong` | `byteops.flip_long` | exact | proven | authoritative | ? |  |
| `4:7438` | `_CopyName` | `netbios.copy_name` | exact | proven | authoritative | ? |  |
| `5:0ACC` | `_PlaceDrop` | `gameplay.place_drop` | exact | proven | authoritative | far |  |
| `5:0B76` | `_InitWater` | `gameplay.init_water` | exact | proven | authoritative | ? |  |
| `5:0B8A` | `_AddWater` | `gameplay.add_water` | exact | proven | authoritative | far |  |
| `5:0C54` | `_DropWater` | `gameplay.drop_water` | exact | proven | authoritative | ? |  |
| `5:0D18` | `_PickupFoodA` | `gameplay.pickup_food_a` | exact | proven | authoritative | far |  |
| `5:0D86` | `_DropFoodA` | `gameplay.drop_food_a` | exact | proven | authoritative | far |  |
| `5:0EAA` | `_FoodFall` | `gameplay.food_fall` | exact | proven | authoritative | far |  |
| `5:0F40` | `_PickupFoodB` | `gameplay.pickup_food_b` | exact | proven | authoritative | far |  |
| `5:0FA2` | `_PickupFoodR` | `gameplay.pickup_food_r` | exact | proven | authoritative | far |  |
| `5:1004` | `_PlaceEggB` | `gameplay.place_egg_b` | exact | proven | authoritative | far |  |
| `5:1068` | `_PlaceEggR` | `gameplay.place_egg_r` | exact | proven | authoritative | far |  |
| `5:10CC` | `_GetDir` | `gameplay.get_dir` | exact | proven | authoritative | ? |  |
| `5:1122` | `_GetDis` | `gameplay.get_dis` | exact | proven | authoritative | ? |  |
| `5:115C` | `_InNestBounds` | `gameplay.in_nest_bounds` | exact | proven | authoritative | ? |  |
| `5:1182` | `_IsItDirt` | `gameplay.is_it_dirt` | exact | proven | authoritative | ? |  |
| `5:119C` | `_GetExitDirB` | `gameplay.get_exit_dir_b` | exact | proven | authoritative | far |  |
| `5:1240` | `_GetExitDirR` | `gameplay.get_exit_dir_r` | exact | proven | authoritative | far |  |
| `5:12E4` | `_GetEnterDirB` | `gameplay.get_enter_dir_b` | exact | proven | authoritative | far |  |
| `5:137C` | `_GetEnterDirR` | `gameplay.get_enter_dir_r` | exact | proven | authoritative | far |  |
| `5:147C` | `_SGIRand` | `gameplay.sg_i_rand` | exact | proven | authoritative | ? |  |
| `5:14A4` | `_SGRand` | `gameplay.sg_rand` | exact | proven | authoritative | ? |  |
| `5:14CC` | `_SGSRand` | `gameplay.sg_s_rand` | exact | proven | authoritative | ? |  |
| `5:156E` | `_RRand` | `simone.r_rand` | exact | proven | authoritative | far |  |
| `5:158A` | `_SRand1` | `simone.srand1` | exact | proven | authoritative | ? |  |
| `5:15AE` | `_SRand2` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15CE 5:15EE 5:160E 5:162E 5:164E 5:166E 5:168E |
| `5:15CE` | `_SRand4` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15EE 5:160E 5:162E 5:164E 5:166E 5:168E |
| `5:15EE` | `_SRand8` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:160E 5:162E 5:164E 5:166E 5:168E |
| `5:160E` | `_SRand16` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:15EE 5:162E 5:164E 5:166E 5:168E |
| `5:162E` | `_SRand32` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:15EE 5:160E 5:164E 5:166E 5:168E |
| `5:164E` | `_SRand64` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:15EE 5:160E 5:162E 5:166E 5:168E |
| `5:166E` | `_SRand128` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:15EE 5:160E 5:162E 5:164E 5:168E |
| `5:168E` | `_SRand256` | `simone.srand_pow2` | derived | proven | authoritative | ? | twins: 5:15AE 5:15CE 5:15EE 5:160E 5:162E 5:164E 5:166E |
| `5:16AE` | `_DigMyNewHole` | `gameplay.dig_my_new_hole` | exact | proven | authoritative | far |  |
| `5:171A` | `_CreateNewHole` | `gameplay.create_new_hole` | exact | proven | authoritative | far |  |
| `5:1914` | `_DigMyTile` | `gameplay.dig_my_tile` | exact | proven | authoritative | far |  |
| `5:1B06` | `_MakeNewHoleB` | `gameplay.make_new_hole_b` | exact | proven | authoritative | far |  |
| `5:1CBA` | `_CanBeHouseHole` | `gameplay.can_be_house_hole` | exact | proven | authoritative | far |  |
| `5:1D02` | `_MakeNewHoleR` | `gameplay.make_new_hole_r` | exact | proven | authoritative | ? |  |
| `5:1F8E` | `_HoleBorder` | `gameplay.hole_border` | exact | proven | authoritative | far |  |
| `5:1FE4` | `_DigTileB` | `gameplay.dig_tile_b` | exact | proven | authoritative | far |  |
| `5:21DE` | `_DigTileR` | `gameplay.dig_tile_r` | exact | proven | authoritative | far |  |
| `5:22D4` | `_DigTileThemB` | `gameplay.dig_tile_them_b` | exact | proven | authoritative | far |  |
| `5:241C` | `_DigTileThemR` | `gameplay.dig_tile_them_r` | exact | proven | authoritative | far |  |
| `5:255A` | `_SmoothEdgesB` | `gameplay.smooth_edges_b` | exact | proven | authoritative | ? |  |
| `5:26C4` | `_RIsItDirt` | `gameplay.r_is_it_dirt` | exact | proven | authoritative | ? |  |
| `5:26E4` | `_SmoothEdgesR` | `gameplay.smooth_edges_r` | exact | proven | authoritative | ? |  |
| `5:284E` | `_FixExitMapB` | `gameplay.fix_exit_map_b` | exact | proven | authoritative | ? |  |
| `5:2914` | `_FixExitMapR` | `gameplay.fix_exit_map_r` | exact | proven | authoritative | ? |  |
| `5:29DA` | `_FloodNestB` | `gameplay.flood_nest_b` | exact | proven | authoritative | ? |  |
| `5:2A16` | `_CompactListA` | `gameplay.compact_list_a` | exact | proven | authoritative | ? |  |
| `5:2A7A` | `_CompactListB` | `gameplay.compact_list_b` | exact | proven | authoritative | ? |  |
| `5:2ADE` | `_CompactListR` | `gameplay.compact_list_r` | exact | proven | authoritative | ? |  |
| `5:2B42` | `_RemoveFromAList` | `gameplay.remove_from_a_list` | exact | proven | authoritative | ? |  |
| `5:2C42` | `_FindInAList` | `gameplay.find_in_a_list` | exact | proven | authoritative | ? |  |
| `5:2C86` | `_FindInBList` | `gameplay.find_in_b_list` | exact | proven | authoritative | ? |  |
| `5:2CCE` | `_FindInRList` | `gameplay.find_in_r_list` | exact | proven | authoritative | ? |  |
| `5:2D16` | `_DrownBList` | `gameplay.drown_b_list` | exact | proven | authoritative | ? |  |
| `5:2D66` | `_DrownRList` | `gameplay.drown_r_list` | exact | proven | authoritative | ? |  |
| `5:2DB6` | `_ExitHole` | `gameplay.exit_hole` | exact | proven | authoritative | ? |  |
| `5:2EF0` | `_AddAntToAList` | `gameplay.add_ant_to_a_list` | exact | proven | authoritative | ? |  |
| `5:2F4A` | `_AddAntToBList` | `gameplay.add_ant_to_b_list` | exact | proven | authoritative | ? |  |
| `5:2FA4` | `_AddAntToRList` | `gameplay.add_ant_to_r_list` | exact | proven | authoritative | ? |  |
| `5:2FFE` | `_GetFromAlist` | `gameplay.get_from_a_list` | exact | proven | authoritative | far |  |
| `5:3046` | `_BuildAntListA` | `gameplay.build_ant_list_a` | exact | proven | authoritative | far |  |
| `5:30E8` | `_ClearListB` | `gameplay.clear_list_b` | exact | proven | authoritative | ? |  |
| `5:30F4` | `_ClearListR` | `gameplay.clear_list_r` | exact | proven | authoritative | ? |  |
| `5:3698` | `_MakeKitchenWall` | `gameplay.make_kitchen_wall` | exact | proven | authoritative | far |  |
| `5:3944` | `_TileFrame1` | `gameplay.tile_frame1` | exact | proven | authoritative | far |  |
| `5:3AA2` | `_TileFrame2` | `gameplay.tile_frame2` | exact | proven | authoritative | far |  |
| `5:3C00` | `_MakeOutletV` | `gameplay.make_outlet_v` | exact | proven | authoritative | far |  |
| `5:3D02` | `_MakePlugV` | `gameplay.make_plug_v` | exact | proven | authoritative | far |  |
| `5:3D44` | `_MakeOutletH` | `gameplay.make_outlet_h` | exact | proven | authoritative | far |  |
| `5:3E46` | `_MakePlugH` | `gameplay.make_plug_h` | exact | proven | authoritative | far |  |
| `5:3E88` | `_MakeKnob` | `gameplay.make_knob` | exact | proven | authoritative | far |  |
| `5:3ECA` | `_MakePenny` | `gameplay.make_penny` | exact | proven | authoritative | far |  |
| `5:3F0C` | `_MakeClip` | `gameplay.make_clip` | exact | proven | authoritative | far |  |
| `5:3F54` | `_FillMap` | `gameplay.fill_map` | exact | proven | authoritative | far |  |
| `5:5362` | `_ScanForAnts` | `gameplay.scan_for_ants` | exact | proven | authoritative | far |  |
| `5:53D4` | `_KillSpider` | `gameplay.kill_spider` | exact | proven | authoritative | ? |  |
| `5:53F6` | `_SFoundAnt` | `gameplay.s_found_ant` | exact | proven | authoritative | ? |  |
| `5:56BA` | `_SGetDis` | `gameplay.s_get_dis` | exact | proven | authoritative | ? |  |
| `5:56DA` | `_IsValidLocation` | `gameplay.is_valid_location` | exact | proven | authoritative | ? |  |
| `5:5720` | `_IsYellowAnt` | `gameplay.is_yellow_ant` | exact | proven | authoritative | ? |  |
| `5:573C` | `_GetAntIndex` | `gameplay.get_ant_index` | exact | proven | authoritative | ? |  |
| `5:584A` | `_SetAntIndex` | `gameplay.set_ant_index` | exact | proven | authoritative | ? |  |
| `5:5922` | `_FindLifeIndex` | `gameplay.find_life_index` | exact | proven | authoritative | far |  |
| `5:59FC` | `_FindAntIndex` | `gameplay.find_ant_index` | exact | proven | authoritative | ? |  |
| `5:5AD2` | `_IsClear3x3` | `gameplay.is_clear_3x3` | exact | proven | authoritative | ? |  |
| `5:5B2C` | `_IsClearTile` | `gameplay.is_clear_tile` | exact | proven | authoritative | ? |  |
| `5:5EC8` | `_IsThisEgg` | `gameplay.is_this_egg` | exact | proven | authoritative | ? |  |
| `5:5EE4` | `_IsThisGrass` | `gameplay.is_this_grass` | exact | proven | authoritative | ? |  |
| `5:5F04` | `_IsThisFood` | `gameplay.is_this_food` | exact | proven | authoritative | ? |  |
| `5:5F32` | `_IsThisPebble` | `gameplay.is_this_pebble` | exact | proven | authoritative | ? |  |
| `5:5F64` | `_IsItNFood` | `gameplay.is_it_nfood` | exact | proven | authoritative | ? |  |
| `5:5F7E` | `_IsItFoodAt` | `gameplay.is_it_food_at` | exact | proven | authoritative | far |  |
| `5:6040` | `_GetLife` | `gameplay.life_cell_offset` | exact | proven | authoritative | ? |  |
| `5:60E2` | `_GetMap` | `gameplay.map_cell_offset` | exact | proven | authoritative | ? |  |
| `5:617A` | `_SetMap` | `gameplay.set_map` | exact | proven | authoritative | ? |  |
| `5:88A2` | `_FindEggAt` | `gameplay.find_egg_at` | exact | proven | authoritative | ? |  |
| `5:8A96` | `_FindLifeAt` | `gameplay.find_life_at` | exact | proven | authoritative | far |  |
| `5:8C70` | `_SetMyHealth` | `gameplay.set_my_health` | exact | proven | authoritative | ? |  |
| `5:9342` | `_TileCanBeMovedOn` | `gameplay.tile_can_be_moved_on` | exact | proven | authoritative | ? |  |
| `5:94A0` | `_IsNotBarrier` | `gameplay.is_not_barrier` | exact | proven | authoritative | ? |  |
| `5:94C6` | `_IsNotObstacle` | `gameplay.is_not_obstacle` | exact | proven | authoritative | ? |  |
| `5:95C6` | `_IsItDigable` | `gameplay.is_it_digable` | exact | proven | authoritative | ? |  |
| `5:96B6` | `_IsItYellow` | `gameplay.is_it_yellow` | exact | proven | authoritative | far |  |
| `5:9784` | `_IsLessThanHole` | `gameplay.is_less_than_hole` | exact | proven | authoritative | ? |  |
| `5:97AA` | `_IsSamePlane` | `gameplay.is_same_plane` | exact | proven | authoritative | ? |  |
| `5:97CA` | `_IsLiftable` | `gameplay.is_liftable` | exact | proven | authoritative | far |  |
| `5:9B4A` | `_IsItAHole` | `gameplay.is_it_a_hole` | exact | proven | authoritative | ? |  |
| `5:9C02` | `_IsValidA` | `gameplay.is_valid_a` | exact | proven | authoritative | ? |  |
| `5:9C26` | `_IsValidB` | `gameplay.is_valid_b` | exact | proven | authoritative | ? |  |
| `6:034A` | `_ClrModePop` | `gameplay.clr_mode_pop` | exact | proven | authoritative | ? |  |
| `6:038E` | `_TallyModePop` | `gameplay.tally_mode_pop` | exact | proven | authoritative | ? |  |
| `6:0474` | `_FeedAnts` | `gameplay.feed_ants` | exact | proven | authoritative | ? |  |
| `6:0A1C` | `_SimEggA` | `gameplay.sim_egg_a` | exact | proven | authoritative | near |  |
| `6:0A74` | `_SimQueenA` | `gameplay.sim_queen_a` | exact | proven | authoritative | near |  |
| `6:0B1E` | `_LostHeadA` | `gameplay.lost_head_a` | exact | proven | authoritative | near |  |
| `6:0B76` | `_DoRestAnt` | `gameplay.do_rest_ant` | exact | proven | authoritative | near |  |
| `6:0C7A` | `_DoRepoExit` | `gameplay.do_repo_exit` | exact | proven | authoritative | near |  |
| `6:0D4A` | `_DoRepoFly` | `gameplay.do_repo_fly` | exact | proven | authoritative | near |  |
| `6:0E66` | `_DoRandAntA` | `gameplay.do_rand_ant_a` | exact | proven-gated [_DoTroph, _YellowFight] | authoritative | near |  |
| `6:1234` | `_DoRandAntAA` | `gameplay.do_rand_ant_aa` | exact | proven-gated [_YellowFight] | authoritative | near |  |
| `6:1480` | `_DoDigOutAntA` | `gameplay.do_dig_out_ant_a` | exact | proven | authoritative | ? |  |
| `6:1676` | `_DoToNestAnt` | `gameplay.do_to_nest_ant` | exact | proven-gated [_DoTroph, _YellowFight] | authoritative | near |  |
| `6:1A0A` | `_DoToAlarm` | `gameplay.do_to_alarm` | exact | proven-gated [_YellowFight] | authoritative | near |  |
| `6:1CB4` | `_DoReturnFoodAnt` | `gameplay.do_return_food_ant` | exact | proven | authoritative | near |  |
| `6:1E42` | `_DoForageAnt` | `gameplay.do_forage_ant` | exact | proven-gated [_DoTroph, _YellowFight] | authoritative | near |  |
| `6:22A8` | `_DoRecruitAnt` | `gameplay.do_recruit_ant` | exact | proven-gated [_DoTroph, _YellowFight] | authoritative | near |  |
| `6:257A` | `_GoInNest` | `gameplay.go_in_nest` | exact | proven | authoritative | near |  |
| `6:266A` | `_StartFightA` | `gameplay.start_fight_a` | exact | proven | authoritative | near |  |
| `6:26F4` | `_GetWinner` | `gameplay.get_winner` | exact | proven | authoritative | near |  |
| `6:27E6` | `_DoFightA` | `gameplay.do_fight_a` | exact | proven | authoritative | near |  |
| `6:28C0` | `_DeadAntHere` | `gameplay.dead_ant_here` | exact | proven | authoritative | far |  |
| `6:2A22` | `_RandTurn` | `gameplay.rand_turn` | exact | proven | authoritative | near |  |
| `6:2A40` | `_DoAttackAnt` | `gameplay.do_attack_ant` | exact | proven-gated [_YellowFight] | authoritative | near |  |
| `6:2CC0` | `_IsItHole` | `gameplay.is_it_hole` | exact | proven | authoritative | ? |  |
| `6:2D1A` | `_IsItFood` | `gameplay.is_it_food` | exact | proven | authoritative | ? |  |
| `6:2D4E` | `_DoAntSimB` | `gameplay.do_ant_sim_b` | exact | proven | authoritative | near |  |
| `6:2DAE` | `_DoNestAntB` | `gameplay.do_nest_ant_b` | exact | proven | authoritative | far |  |
| `6:3524` | `_RaidInB` | `gameplay.raid_in_b` | exact | proven | authoritative | far |  |
| `6:3610` | `_RaidOutB` | `gameplay.raid_out_b` | exact | proven | authoritative | far |  |
| `6:367E` | `_DoRestB` | `gameplay.do_rest_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:37A4` | `_DoDrownB` | `gameplay.do_drown_b` | exact | proven | authoritative | far |  |
| `6:3876` | `_DoRandB` | `gameplay.do_rand_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:3A54` | `_DoNestFightB` | `gameplay.do_nest_fight_b` | exact | proven | authoritative | far |  |
| `6:3BA2` | `_CheckNestFightB` | `gameplay.check_nest_fight_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:3C3C` | `_DropFoodB` | `gameplay.drop_food_b` | exact | proven | authoritative | ? |  |
| `6:3CA0` | `_SimEggB` | `gameplay.sim_egg_b` | exact | proven | authoritative | far |  |
| `6:3DC2` | `_SimQueenB` | `gameplay.sim_queen_b` | exact | proven | authoritative | far |  |
| `6:405E` | `_GetBestDir` | `gameplay.get_best_dir` | exact | proven | authoritative | ? |  |
| `6:4154` | `_QueenMoveB` | `gameplay.queen_move_b` | exact | proven | authoritative | far |  |
| `6:424A` | `_MakeNewTailB` | `gameplay.make_new_tail_b` | exact | proven | authoritative | far |  |
| `6:42B0` | `_KillTailB` | `gameplay.kill_tail_b` | exact | proven | authoritative | ? |  |
| `6:42DE` | `_LostHeadB` | `gameplay.lost_head_b` | exact | proven | authoritative | far |  |
| `6:433C` | `_LostTailB` | `gameplay.lost_tail_b` | exact | proven | authoritative | far |  |
| `6:439E` | `_TryMoveDirB` | `gameplay.try_move_dir_b` | exact | proven-gated [_DoTroph] | authoritative | far |  |
| `6:44A8` | `_DoNestingB` | `gameplay.do_nesting_b` | exact | proven | authoritative | far |  |
| `6:47C6` | `_TryEatFoodB` | `gameplay.try_eat_food_b` | exact | proven | authoritative | far |  |
| `6:4844` | `_EatFoodB` | `gameplay.eat_food_b` | exact | proven | authoritative | far |  |
| `6:48B4` | `_StealFoodB` | `gameplay.steal_food_b` | exact | proven | authoritative | far |  |
| `6:48F8` | `_DecEatB` | `gameplay.dec_eat_b` | exact | proven | authoritative | ? |  |
| `6:492A` | `_DoFoodInB` | `gameplay.do_food_in_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:4BD0` | `_DoDigInB` | `gameplay.do_dig_in_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:4EB0` | `_DoDigOutB` | `gameplay.do_dig_out_b` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:515E` | `_LeaveNestB` | `gameplay.leave_nest_b` | exact | proven | authoritative | far |  |
| `6:520A` | `_GetOutB` | `gameplay.get_out_b` | exact | proven | authoritative | far |  |
| `6:5B2A` | `_RaidInR` | `gameplay.raid_in_r` | exact | proven | authoritative | far |  |
| `6:5C16` | `_StayInR` | `gameplay.stay_in_r` | exact | proven | authoritative | far |  |
| `6:5D10` | `_RaidOutR` | `gameplay.raid_out_r` | exact | proven | authoritative | far |  |
| `6:5D7E` | `_DoRestR` | `gameplay.do_rest_r` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:5EA8` | `_DoDrownR` | `gameplay.do_drown_r` | exact | proven | authoritative | far |  |
| `6:5F7A` | `_DoRandR` | `gameplay.do_rand_r` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:6072` | `_DoNestFightR` | `gameplay.do_nest_fight_r` | exact | proven | authoritative | far |  |
| `6:61A2` | `_CheckNestFightR` | `gameplay.check_nest_fight_r` | exact | proven-gated [_YellowFight] | authoritative | far |  |
| `6:6242` | `_DropFoodR` | `gameplay.drop_food_r` | exact | proven | authoritative | ? |  |
| `6:62A6` | `_SimEggR` | `gameplay.sim_egg_r` | exact | proven | authoritative | far |  |
| `6:6606` | `_QueenMoveR` | `gameplay.queen_move_r` | exact | proven | authoritative | far |  |
| `6:66FC` | `_MakeNewTailR` | `gameplay.make_new_tail_r` | exact | proven | authoritative | far |  |
| `6:6762` | `_KillTailR` | `gameplay.kill_tail_r` | exact | proven | authoritative | ? |  |
| `6:6790` | `_LostHeadR` | `gameplay.lost_head_r` | exact | proven | authoritative | far |  |
| `6:67EE` | `_LostTailR` | `gameplay.lost_tail_r` | exact | proven | authoritative | far |  |
| `6:6850` | `_TryMoveDirR` | `gameplay.try_move_dir_r` | exact | proven | authoritative | far |  |
| `6:690A` | `_DoNestingR` | `gameplay.do_nesting_r` | exact | proven | authoritative | far |  |
| `6:6B38` | `_TryEatFoodR` | `gameplay.try_eat_food_r` | exact | proven | authoritative | far |  |
| `6:6BB6` | `_EatFoodR` | `gameplay.eat_food_r` | exact | proven | authoritative | far |  |
| `6:6C26` | `_StealFoodR` | `gameplay.steal_food_r` | exact | proven | authoritative | far |  |
| `6:6C6A` | `_DecEatR` | `gameplay.dec_eat_r` | exact | proven | authoritative | ? |  |
| `6:74BA` | `_GetOutR` | `gameplay.get_out_r` | exact | proven | authoritative | far |  |
| `6:8682` | `_GetMyDis` | `gameplay.get_my_dis` | exact | proven | authoritative | far |  |
| `6:8828` | `_GetMyBestDirs` | `gameplay.get_my_best_dirs` | exact | proven | authoritative | far |  |
| `6:8928` | `_GetMyRandDirs` | `gameplay.get_my_rand_dirs` | exact | proven | authoritative | far |  |
| `6:8B40` | `_CheckMyBestDirs` | `gameplay.check_my_best_dirs` | exact | proven | authoritative | far |  |
| `6:8BEA` | `_GetMyNextRandDirs` | `gameplay.get_my_next_rand_dirs` | exact | proven | authoritative | ? |  |
| `6:8CDE` | `_GetMyInitialRandDir` | `gameplay.get_my_initial_rand_dir` | exact | proven | authoritative | far |  |
| `6:8D3A` | `_GetMyBestDir` | `gameplay.get_my_best_dir` | exact | proven | authoritative | far |  |
| `6:8ECA` | `_GetMyDir` | `gameplay.get_my_dir` | exact | proven | authoritative | far |  |
| `6:91DE` | `_FillHolesBN` | `gameplay.fill_holes_bn` | exact | proven | authoritative | ? |  |
| `6:9244` | `_FillHolesRN` | `gameplay.fill_holes_rn` | exact | proven | authoritative | ? |  |
| `6:92AA` | `_ColonySmellBN` | `gameplay.colony_smell_decay_bn` | exact | proven | authoritative | ? |  |
| `6:92D8` | `_ColonySmellRN` | `gameplay.colony_smell_decay_rn` | exact | proven | authoritative | ? |  |
| `6:9306` | `_ColonySmellBT` | `gameplay.colony_smell_decay_bt` | exact | proven | authoritative | ? |  |
| `6:9344` | `_ColonySmellRT` | `gameplay.colony_smell_decay_rt` | exact | proven | authoritative | ? |  |
| `6:9380` | `_SmoothAlarm` | `gameplay.smooth_alarm` | exact | proven | authoritative | ? |  |
| `6:943C` | `_AlarmHere` | `gameplay.alarm_here` | exact | proven | authoritative | ? |  |
| `6:947E` | `_AlarmHere2` | `gameplay.alarm_here2` | exact | proven | authoritative | ? |  |
| `6:94B6` | `_JamScentBN` | `gameplay.jam_scent_bn` | exact | proven | authoritative | ? |  |
| `6:94F6` | `_JamScentRN` | `gameplay.jam_scent_rn` | exact | proven | authoritative | ? |  |
| `6:9536` | `_JamScentBT` | `gameplay.jam_scent_bt` | exact | proven | authoritative | ? |  |
| `6:9576` | `_JamScentRT` | `gameplay.jam_scent_rt` | exact | proven | authoritative | ? |  |
| `6:95B6` | `_DecTSmell` | `gameplay.dec_t_smell` | exact | proven | authoritative | ? |  |
| `6:9612` | `_GetSmellT` | `gameplay.get_smell_t` | exact | proven | authoritative | ? |  |
| `6:967C` | `_MakeRedInitiator` | `gameplay.make_red_initiator` | exact | proven | authoritative | ? |  |
| `6:96D4` | `_DoRedInitiator` | `gameplay.do_red_initiator` | exact | proven-gated [_GetRedBestDirs] | authoritative | far |  |
| `6:9940` | `_GetNewRedTask` | `gameplay.get_new_red_task` | exact | proven | authoritative | ? |  |
| `6:9A18` | `_GetRedBestDirs` | `gameplay.get_red_best_dirs` | exact | proven | authoritative | far |  |
| `7:0000` | `_GetStrategy` | `gameplay.get_strategy` | exact | proven | authoritative | ? |  |
| `7:01CC` | `_GstrB` | `gameplay.gstr_b` | exact | proven | authoritative | ? |  |
| `7:026E` | `_SetCasteProd` | `gameplay.set_caste_prod` | exact | proven | authoritative | ? |  |
| `7:0326` | `_SetModeProd` | `gameplay.set_mode_prod` | exact | proven | authoritative | ? |  |
| `7:03C2` | `_GstrR` | `gameplay.gstr_r` | exact | proven | authoritative | ? |  |
| `7:050E` | `_StartAttack` | `gameplay.start_attack` | exact | proven | authoritative | ? |  |
| `7:0550` | `_ForceModeA` | `gameplay.force_mode_a` | exact | proven | authoritative | far |  |
| `7:0622` | `_ForceModeB` | `gameplay.force_mode_b` | exact | proven | authoritative | far |  |
| `7:06D2` | `_Recruit` | `gameplay.recruit` | exact | proven | authoritative | far |  |
| `7:078A` | `_UnRecruit` | `gameplay.un_recruit` | exact | proven | authoritative | far |  |
| `7:0866` | `_RecruitRed` | `gameplay.recruit_red` | exact | proven | authoritative | far |  |
| `7:08DA` | `_UnRecruitRed` | `gameplay.un_recruit_red` | exact | proven | authoritative | ? |  |
| `7:0910` | `_GetNewMode` | `gameplay.get_new_mode` | exact | proven | authoritative | far |  |
| `7:09D0` | `_GetNewModeB` | `gameplay.get_new_mode_b` | exact | proven | authoritative | far |  |
| `7:0A50` | `_GetNewModeR` | `gameplay.get_new_mode_r` | exact | proven | authoritative | far |  |
| `7:0AB0` | `_GetForageDir` | `gameplay.get_forage_dir` | exact | proven | authoritative | far |  |
| `7:0C30` | `_GetNestDir` | `gameplay.get_nest_dir` | exact | proven | authoritative | far |  |
| `7:0E54` | `_GetAlarmDir` | `gameplay.get_alarm_dir` | exact | proven | authoritative | far |  |
| `7:0F72` | `_GetRandDir` | `gameplay.get_rand_dir` | exact | proven | authoritative | far |  |
| `7:1026` | `_GetDefendDir` | `gameplay.get_defend_dir` | exact | proven | authoritative | far |  |
| `7:1194` | `_GetRedDefendDir` | `gameplay.get_red_defend_dir` | exact | proven | authoritative | far |  |
| `7:12EC` | `_Bounce` | `gameplay.bounce` | exact | proven | authoritative | far |  |
| `7:1378` | `_InitSimYard` | `gameplay.init_sim_yard` | exact | proven | authoritative | ? |  |
| `7:203E` | `_NotMowed` | `gameplay.not_mowed` | exact | proven | authoritative | far |  |
| `7:2072` | `_IsValidYard` | `gameplay.is_valid_yard` | exact | proven | authoritative | ? |  |
| `7:2096` | `_InitGrassMap` | `gameplay.init_grass_map` | exact | proven | authoritative | far |  |
| `7:32A6` | `_FollowCatDir` | `gameplay.follow_cat_dir` | exact | proven | authoritative | ? |  |
| `7:3580` | `_MaintainSwarm` | `gameplay.maintain_swarm` | exact | proven | authoritative | far |  |
| `7:3CE4` | `_GetNearbyPatches` | `gameplay.get_nearby_patches` | exact | proven | authoritative | far |  |
| `7:3D4C` | `_Reproduce` | `gameplay.reproduce` | exact | proven | authoritative | far |  |
| `7:3DF2` | `_StartMigrate` | `gameplay.start_migrate` | exact | proven | authoritative | far |  |
| `7:3E6C` | `_EndMigrate` | `gameplay.end_migrate` | exact | proven | authoritative | far |  |
| `7:3EF8` | `_InitSow` | `gameplay.init_sow` | exact | proven | authoritative | ? |  |
| `7:3F8A` | `_DoSow` | `gameplay.do_sow` | exact | proven | authoritative | ? |  |
| `7:40C6` | `_InitAntLions` | `gameplay.init_ant_lions` | exact | proven | authoritative | far |  |
| `7:4222` | `_AddRandAntLion` | `gameplay.add_rand_ant_lion` | exact | proven | authoritative | far |  |
| `7:4340` | `_AddAntLion` | `gameplay.add_ant_lion` | exact | proven | authoritative | far |  |
| `7:4AD8` | `_SetAntLion` | `gameplay.set_ant_lion` | exact | proven | authoritative | far |  |
| `7:4B12` | `_FindInLionList` | `gameplay.find_in_lion_list` | exact | proven | authoritative | far |  |
| `7:4B58` | `_KillAntLion` | `gameplay.kill_ant_lion` | exact | proven | authoritative | far |  |
| `7:4BF8` | `_InitPillar` | `gameplay.init_pillar` | exact | proven | authoritative | ? |  |
| `7:4CDC` | `_DoPillar` | `gameplay.do_pillar` | exact | proven | authoritative | ? |  |
| `7:5304` | `_StorePillarMap` | `gameplay.store_pillar_map` | exact | proven | authoritative | far |  |
| `7:5372` | `_ReplacePillarMap` | `gameplay.replace_pillar_map` | exact | proven | authoritative | far |  |
| `7:53DA` | `_MakeAPill` | `gameplay.make_a_pill` | exact | proven | authoritative | ? |  |
| `7:56DA` | `_PlacePillTile` | `gameplay.place_pill_tile` | exact | proven | authoritative | far |  |
| `7:5702` | `_PillGetLife` | `gameplay.pill_get_life` | exact | proven | authoritative | far |  |
| `7:572A` | `_IsPillDead` | `gameplay.is_pill_dead` | exact | proven | authoritative | ? |  |
| `7:57D2` | `_MakePillFood` | `gameplay.make_pill_food` | exact | proven | authoritative | far |  |
| `7:5A02` | `_PillFoodTile` | `gameplay.pill_food_tile` | exact | proven | authoritative | far |  |
| `7:5A70` | `_InitSimVars` | `gameplay.init_sim_vars` | exact | proven | authoritative | ? |  |
| `7:62DE` | `_DigOutBNest` | `gameplay.dig_out_b_nest` | exact | proven | authoritative | far |  |
| `7:63B8` | `_DigOutRNest` | `gameplay.dig_out_r_nest` | exact | proven | authoritative | far |  |
| `7:65CE` | `_PlaceBlackQueen` | `gameplay.place_black_queen` | exact | proven | authoritative | far |  |
| `7:671A` | `_MakeBlkQueen` | `gameplay.make_blk_queen` | exact | proven | authoritative | far |  |
| `7:67DA` | `_PlaceRedQueen` | `gameplay.place_red_queen` | exact | proven | authoritative | ? |  |
| `7:6906` | `_MakeRedQueen` | `gameplay.make_red_queen` | exact | proven | authoritative | far |  |
| `7:69C8` | `_fracSIN` | `gameplay.frac_sin` | exact | proven | authoritative | far |  |
| `7:6A0E` | `_fracCOS` | `gameplay.frac_cos` | exact | proven | authoritative | far |  |
| `7:6A58` | `_AddFood` | `gameplay.add_food` | exact | proven | authoritative | far |  |
| `7:6C5A` | `_AddBlackAnts` | `gameplay.add_black_ants` | exact | proven | authoritative | far |  |
| `7:6CFE` | `_AddRedAnts` | `gameplay.add_red_ants` | exact | proven | authoritative | far |  |
| `7:6DAC` | `_GrabMap` | `gameplay.grab_map` | exact | proven | authoritative | far |  |
| `7:6DEC` | `_ClrArrays` | `gameplay.clr_arrays` | exact | proven | authoritative | ? |  |
| `7:A668` | `_Unpack` | `lzss.decode_chunk` | exact | proven | authoritative | ? | streaming resumable decoder; ASM ABI is far cdecl (dst=[bp+6:8], budget=[bp+10]) with cross-call state in DGROUP B7C0..B7D4 -- pure-core signature differs, see hooks._make_unpack_island for the marshalling |
| `7:B033` | `_DrawChar` | `render.draw_char` | exact | proven | authoritative | ? |  |
| `7:C256` | `_win_IsWinOpen` | `window.win_is_win_open` | exact | proven | authoritative | ? |  |
| `7:C2D2` | `_win_GetObjRect` | `window.win_get_obj_rect` | exact | proven | authoritative | ? |  |

## 3. Helper-split / non-matched functions

These have **no SYM entry of their own** — they are named pieces of a parent
function's recovery (the original was one routine the Python splits for
readability, or several byte-identical B/R parents share one private core).
The adapter stage never routes to these directly; they are reached through
their parents. `key: null` in the JSON.

| impl | status | parent(s) | notes |
|---|---|---|---|
| `crt_math._sx32` | utility-helper | — | sign-extend idiom |
| `gameplay._acc_add32` | helper-split | `gameplay._dig_tile_reroll_and_track`, `gameplay._do_drown`, `gameplay._do_nest_ant_b_foreign`, `gameplay.dig_tile_them_b`, `gameplay.dig_tile_them_r`, `gameplay.do_nest_ant_b`, `gameplay.sim_egg_b` |  |
| `gameplay._add_ants` | helper-split | `gameplay.add_black_ants`, `gameplay.add_red_ants` |  |
| `gameplay._cell_offset` | helper-split | `gameplay.life_cell_offset`, `gameplay.map_cell_offset` |  |
| `gameplay._clear_3x3` | helper-split | `gameplay.dig_my_new_hole`, `gameplay.make_new_hole_b`, `gameplay.make_new_hole_r` |  |
| `gameplay._colony_decay_exponential` | helper-split | `gameplay.colony_smell_decay_bt`, `gameplay.colony_smell_decay_rt` |  |
| `gameplay._colony_decay_linear` | helper-split | `gameplay.colony_smell_decay_bn`, `gameplay.colony_smell_decay_rn` |  |
| `gameplay._compact_list` | helper-split | `gameplay.compact_list_a`, `gameplay.compact_list_b`, `gameplay.compact_list_r` |  |
| `gameplay._dig_in_b_mode_refresh` | helper-split | `gameplay.do_dig_in_b` |  |
| `gameplay._dig_out_nest` | helper-split | `gameplay.dig_out_b_nest`, `gameplay.dig_out_r_nest` |  |
| `gameplay._dig_tile_reroll_and_track` | helper-split | `gameplay.dig_tile_b`, `gameplay.dig_tile_r`, `gameplay.make_new_hole_r` |  |
| `gameplay._do_drown` | helper-split | `gameplay.do_drown_b`, `gameplay.do_drown_r` |  |
| `gameplay._do_nest_ant_b_foreign` | helper-split gates: _YellowFight | `gameplay.do_nest_ant_b` |  |
| `gameplay._drop_food` | helper-split | `gameplay.drop_food_b`, `gameplay.drop_food_r` |  |
| `gameplay._drown_list` | helper-split | `gameplay.drown_b_list`, `gameplay.drown_r_list` |  |
| `gameplay._eat_food` | helper-split | `gameplay.do_food_in_b`, `gameplay.eat_food_b`, `gameplay.eat_food_r` |  |
| `gameplay._fill_holes` | helper-split | `gameplay.fill_holes_bn`, `gameplay.fill_holes_rn` |  |
| `gameplay._fix_exit_map` | helper-split | `gameplay.fix_exit_map_b`, `gameplay.fix_exit_map_r` |  |
| `gameplay._food_growth_trigger` | helper-split | `gameplay._eat_food`, `gameplay._try_eat_food` |  |
| `gameplay._forage_jitter` | helper-split | `gameplay.do_attack_ant`, `gameplay.do_forage_ant`, `gameplay.do_rand_ant_a`, `gameplay.do_rand_ant_aa`, `gameplay.do_recruit_ant`, `gameplay.do_to_alarm`, `gameplay.do_to_nest_ant` |  |
| `gameplay._frac_trig` | helper-split | `gameplay.frac_cos`, `gameplay.frac_sin` |  |
| `gameplay._get_enter_dir` | helper-split | `gameplay.get_enter_dir_b`, `gameplay.get_enter_dir_r` |  |
| `gameplay._get_exit_dir` | helper-split | `gameplay.get_exit_dir_b`, `gameplay.get_exit_dir_r` |  |
| `gameplay._is_it_digable_at` | helper-split | `gameplay.dig_my_tile` |  |
| `gameplay._jam_scent` | helper-split | `gameplay.jam_scent_bn`, `gameplay.jam_scent_bt`, `gameplay.jam_scent_rn`, `gameplay.jam_scent_rt` |  |
| `gameplay._lost_head` | helper-split | `gameplay.lost_head_b`, `gameplay.lost_head_r` |  |
| `gameplay._lost_tail` | helper-split | `gameplay.lost_tail_b`, `gameplay.lost_tail_r` |  |
| `gameplay._make_new_tail` | helper-split | `gameplay.make_new_tail_b`, `gameplay.make_new_tail_r` |  |
| `gameplay._nest_ant_b_selfcheck` | helper-split gates: _YellowFight | `gameplay.do_nest_ant_b` |  |
| `gameplay._paint_pillar_arm` | helper-split | `gameplay.make_pill_food` |  |
| `gameplay._pickup_food_br` | helper-split | `gameplay.pickup_food_b`, `gameplay.pickup_food_r` |  |
| `gameplay._pillar_cache_index` | helper-split | `gameplay._paint_pillar_arm`, `gameplay.replace_pillar_map`, `gameplay.store_pillar_map` |  |
| `gameplay._place_egg` | helper-split | `gameplay.place_egg_b`, `gameplay.place_egg_r` |  |
| `gameplay._place_two_random_rocks` | helper-split | `gameplay.init_pillar`, `gameplay.init_sow` |  |
| `gameplay._queen_move` | helper-split | `gameplay.queen_move_b`, `gameplay.queen_move_r` |  |
| `gameplay._raid_in` | helper-split | `gameplay.raid_in_b`, `gameplay.raid_in_r` |  |
| `gameplay._raid_out` | helper-split | `gameplay.raid_out_b`, `gameplay.raid_out_r` |  |
| `gameplay._reroll_or_decrement_food_tile` | helper-split | `gameplay._eat_food`, `gameplay._pickup_food_br`, `gameplay._try_eat_food` |  |
| `gameplay._smooth_edges` | helper-split | `gameplay.smooth_edges_b`, `gameplay.smooth_edges_r` |  |
| `gameplay._stamp_glyph` | helper-split | `gameplay.make_clip`, `gameplay.make_knob`, `gameplay.make_outlet_h`, `gameplay.make_outlet_v`, `gameplay.make_penny`, `gameplay.make_plug_h`, `gameplay.make_plug_v` |  |
| `gameplay._steal_food` | helper-split | `gameplay.steal_food_b`, `gameplay.steal_food_r` |  |
| `gameplay._sx16` | utility-helper | — | pervasive sign-extend idiom (59 callers) |
| `gameplay._sx32` | dead-code | — | defined but never called anywhere (dead code) |
| `gameplay._tile_frame` | helper-split | `gameplay.tile_frame1`, `gameplay.tile_frame2` |  |
| `gameplay._try_eat_food` | helper-split | `gameplay.do_dig_in_b`, `gameplay.do_dig_out_b`, `gameplay.try_eat_food_b`, `gameplay.try_eat_food_r` |  |
| `gameplay.get_life_value` | helper-split | `gameplay.life_cell_offset` | value-mask half of the _GetLife recovery |
| `geometry._bisect` | helper-split | `geometry.clip_line` |  |
| `geometry._outcode` | helper-split | `geometry.clip_line` |  |
| `geometry._sar1_sum` | helper-split | `geometry._bisect` |  |
| `lzss.decompress` | api-wrapper | `lzss.decode_chunk` | whole-buffer convenience API over decode_chunk; no ASM twin |
| `netbios.cstrlen` | helper-split | `netbios.copy_name` |  |
| `render._tile_attr` | helper-split | `render.do_calc_tile` |  |
| `render._tile_blit_geometry` | helper-split | `render.xfer_life_tile_color`, `render.xfer_tile_color` |  |
| `render._tile_blit_geometry_mono` | helper-split | `render.xfer_life_tile_mono`, `render.xfer_tile_mono` |  |
| `render.shift_glyph_word` | helper-split | `render.draw_char` |  |
| `simone.srand_step` | helper-split | `simone.srand1`, `simone.srand_pow2` | shared LFSR step of the whole _SRand* family |
| `window._sar16` | helper-split | `window.win_get_obj_rect`, `window.win_is_win_open` |  |

Notable here:

- The **B/R twin pattern** is pervasive: the original compiles near-identical
  black-colony/red-colony routine pairs (`_FixExitMapB`/`_FixExitMapR`, …).
  The corpus gives each SYM entry its own thin public function and factors the
  shared body into one `_private` helper — so the SYM mapping stays 1:1 while
  the logic lives once. Each twin is oracle-tested against its OWN ASM
  instance, not trusted from the sibling.
- `simone.srand_pow2` is the extreme case: EIGHT SYM entries
  (`_SRand2`…`_SRand256`, seg5:15AE…168E) map onto one impl, each compiled
  copy independently byte-tested.
- `gameplay.get_life_value` is the value-masking half of the `_GetLife`
  recovery (`life_cell_offset` is the addressing half); it has no address of
  its own.
- `gameplay._sx32` is **dead code** (defined, never called — `crt_math._sx32`
  is the live one). Flagged `superseded`; safe to delete.

## 4. ABI contracts the generated CPU/ABI adapters need

What the docstrings state mechanically (`args x=[bp+6], y=[bp+8]; FAR
return`), aggregated over the 309 matched entries:

| contract shape | count | notes |
|---|---|---|
| `ret far` stated (far cdecl, caller cleans, args from `[bp+6]` up) | 145 | the default SimAnt C convention; results in AX (predicates/offsets) |
| `ret near` stated (near call, args from `[bp+4]` up) | 20 | intra-segment calls — the seg6 `_Do*Ant` behavior batch is near-return (its dispatcher lives in the same segment); `do_red_initiator` is the one FAR exception in that batch |
| return convention NOT stated in docstring | 144 | see below |
| callee-cleans far (`ret far 8`) | 2 | the CRT helpers `__aFldiv` (4:08D4) and `__aFulmul` (4:096E), dword args, result in DX:AX |
| full named `[bp+N]` arg map recorded | 163 | adapters can be generated directly from the JSON `args` field |
| zero-arg entries | 49 | |
| args known by name only (no `[bp+N]` map) | 96 | ABI hole — close from the island body or disassembly |
| takes at least one state view (`dgroup` / `simant_data_group` / `pack` / `view` / `table_view`) | 240 | the marshalling surface, §7 |
| callback-injected (pure logic over `read_*`/`write_*` closures) | 12 | render/window tier; adapters bind closures to selectors |

View-combination profile of the sim tier (who needs what marshalled): the
modal signature is `(dgroup, simant_data_group, pack, …scalars)` — 92 entries;
then `(dgroup, pack)` 30, `(dgroup)` 23, `(dgroup, simant_data_group)` 21,
`(pack, simant_data_group)` 19 (order in the JSON `views` field is the Python
parameter order, which is NOT always alphabetical). All three views are
**fixed NE data segments** (DGROUP = seg-index 10, SIMANT_DATA_GROUP = 8,
PACK = 9); the pointer globals that reach them are load-time constants
(verified by exhaustive write-scan — see `hooks.py`), so an adapter can bind
them once, not per call.

Known non-stack conventions (do NOT template these):

- `_os_ClipLine` (4:6E24): register args — P0 in SI/DI, P1 in DX/BX, clip
  bounds in DGROUP `0x1D7A`/`0x1D78`, swap-parity left in `0x1D82`, near,
  preserves AX, clobbers CX.
- `_exchange` (4:6E05): ES:DI / DS:SI buffers, CX count, `pushaw`/`popaw`.
- `bytecopy` (2:3460): not a function at all — a mid-routine loop lift inside
  a GR blitter; its "arguments" are the ENCLOSING frame's locals
  (`[bp-8]`/`[bp-12]` huge pointers). No SYM symbol.
- `_Unpack` (7:A668): far cdecl on the surface, but a *resumable streaming*
  decoder with cross-call state in DGROUP `0xB7C0..0xB7D4` (`UnpackState`).
- Renderer tier (`_DoCalcTile`, the MakeTable family, `_DrawChar`): stack args
  plus implicit DGROUP table/global inputs and outputs (e.g. `CE96`/`CE7A`,
  `SS:0x1A56`); the hooks.py island bodies are the precise ABI record.

Where `ret` is `?` (144 entries): the bulk are (a) the render/table tier
whose islands (hooks.py) already encode the exact entry/exit state — **treat
the island body as the ABI oracle** — and (b) pure-value predicates matched by
return-value oracles where the docstring omitted the convention. The JSON
carries `ret: null` rather than a guess, per the fail-loud rule.

## 5. Gates inventory — every raise-loudly `NotImplementedError`

Three unrecovered routines are guarded at their exact call sites; the
recovered callers are otherwise proven byte-exact (each gate branch is covered
by a `pytest.raises(NotImplementedError)` test):

| gate (unrecovered routine) | address | carried by |
|---|---|---|
| `_YellowFight` (yellow-ant combat resolution) | seg6:823E | 16 matched entries: `check_nest_fight_b/r`, `do_rest_b/r`, `do_rand_b/r`, `do_forage_ant`, `do_food_in_b`, `do_dig_out_b`, `do_dig_in_b`, `do_rand_ant_a`, `do_rand_ant_aa`, `do_to_nest_ant`, `do_to_alarm`, `do_recruit_ant`, `do_attack_ant` + 2 helpers (`_nest_ant_b_selfcheck`, `_do_nest_ant_b_foreign`) |
| `_DoTroph` (trophallaxis / food transfer) | seg1:846E | 5 entries: `try_move_dir_b`, `do_forage_ant`, `do_rand_ant_a`, `do_to_nest_ant`, `do_recruit_ant` |
| `_GetRedBestDirs` (red pathfinding variant) | seg6:9A18 | 1 entry: `do_red_initiator` (every real invocation currently hits it) |

Adapter-routing consequence: a call routed into a gated authoritative impl can
raise. The routing layer needs a policy — either catch and fall back to the
lifted original of the *gate target* (`_YellowFight` etc. as lifted code
callable from Python, which the JSON `gates` field makes locatable), or route
the whole parent to the lift until the gate is recovered. Note `_DoTroph`
lives in **seg1** — inside the otherwise-untouched UI segment, so it will
exist as lifted code anyway. `_YellowFight` sits in the 19-symbol seg6
remainder and is the highest-value next recovery: it alone un-gates 16+2
functions.

### 5a. The seg6 remainder (the behavior tier's last 19)

| addr | symbol |
|---|---|
| `6:0000` | `_DoAntSim` |
| `6:02FA` | `_DoSmells` |
| `6:04D8` | `_DoAntSimA` |
| `6:0C1E` | `_DoRepoLoit` |
| `6:0DF6` | `_DoDefendNest` |
| `6:396C` | `_DoRecruitN` |
| `6:5344` | `_DoAntSimR` |
| `6:53A4` | `_DoNestAntR` |
| `6:6386` | `_SimQueenR` |
| `6:6C8C` | `_DoFoodInR` |
| `6:6F0C` | `_DoDigInR` |
| `6:722A` | `_DoDigOutR` |
| `6:75F4` | `_DoAntMoveY` |
| `6:7CF6` | `_DoAntSimY` |
| `6:7E56` | `_AnimYellowFight` |
| `6:8078` | `_AnimYellowInsane` |
| `6:823E` | `_YellowFight` |
| `6:8408` | `_EnterNest` |
| `6:84A4` | `_ExitNest` |

## 6. Islands (`simant/hooks.py`) — classification `island-adapter`

Islands are VM-coupled adapters: installed at CS:IP over a verified prologue
signature, they read CPU state directly, call pure logic, and write back the
exact ABI exit state. In DOS_RE 2.0 terms they are hand-written precursors of
the generated CPU/ABI adapters — **their bodies are the ground-truth ABI
record** for their targets. 61 of 68 already route through a
`simant/recovered/` twin; 7 are island-only (logic small enough that it lives
in the island body — these need a trivial pure re-expression, or direct
adapter emission, in the VM-less layer): `__aFuldiv` (4:0A60, one `//`),
`_XFlipLong` (4:52D8), the four seed accessors `_SetSRandSeed` /
`_GetSRandSeed` / `_SetRRandSeed` / `_GetRRandSeed` (5:1506/1512/1518/151A —
`_GetRRandSeed` reads the BIOS tick dword, a platform effect), and `bytecopy`
(2:3460, not a routine — see §4).

| addr | island | recovered twin | notes |
|---|---|---|---|
| `2:3460` | `bytecopy` | **none (island-only)** | no SYM symbol at address (compiler-emitted mid-routine loop lift, not a function) |
| `4:0A60` | `__aFuldiv` | **none (island-only)** |  |
| `4:442C` | `_WindowsMono_MakeTable4x4a` | `render.windows_mono_make_table_4x4` |  |
| `4:44B9` | `_WindowsMono_MakeTable4x4b` | `render.windows_mono_make_table_4x4` |  |
| `4:4542` | `_WindowsMono_MakeTable2x2a` | `render.windows_mono_make_table_2x2` |  |
| `4:45DB` | `_WindowsMono_MakeTable2x2b` | `render.windows_mono_make_table_2x2` |  |
| `4:4674` | `_Windows_MakeTable4x4` | `render.windows_make_table_4x4` |  |
| `4:46BB` | `_Windows_MakeTable1x1` | `render.windows_make_table_1x1` |  |
| `4:46E9` | `_GenOverMap` | `render.gen_over_map` |  |
| `4:4754` | `_GenNestMap` | `render.gen_nest_map_cells` | listed twice in hooks._ISLANDS (duplicate install, harmless) |
| `4:47DD` | `_XferTileColor` | `render.xfer_tile_color` |  |
| `4:486C` | `_XferTileMono` | `render.xfer_tile_mono` |  |
| `4:48FA` | `_XferLifeTileColor` | `render.xfer_life_tile_color` |  |
| `4:49B7` | `_XferLifeTileMono` | `render.xfer_life_tile_mono` |  |
| `4:4A6B` | `_DoCalcTile` | `render.do_calc_tile` |  |
| `4:52D8` | `_XFlipLong` | **none (island-only)** |  |
| `4:6C62` | `_CopyChar` | `render.copy_char` |  |
| `4:6CAA` | `_CopyCharRep` | `render.copy_char_rep` |  |
| `4:6CF8` | `_MoveTextToBalloon` | `render.move_text_to_balloon` |  |
| `4:6E05` | `_exchange` | `byteops.exchange` |  |
| `4:6E24` | `_os_ClipLine` | `geometry.clip_line` |  |
| `4:7356` | `_FlipWord` | `byteops.flip_word` |  |
| `4:7360` | `_FlipLong` | `byteops.flip_long` |  |
| `4:7438` | `_CopyName` | `netbios.copy_name` |  |
| `5:10CC` | `_GetDir` | `gameplay.get_dir` |  |
| `5:1122` | `_GetDis` | `gameplay.get_dis` |  |
| `5:115C` | `_InNestBounds` | `gameplay.in_nest_bounds` |  |
| `5:1182` | `_IsItDirt` | `gameplay.is_it_dirt` |  |
| `5:1506` | `_SetSRandSeed` | **none (island-only)** |  |
| `5:1512` | `_GetSRandSeed` | **none (island-only)** |  |
| `5:1518` | `_SetRRandSeed` | **none (island-only)** |  |
| `5:151A` | `_GetRRandSeed` | **none (island-only)** |  |
| `5:158A` | `_SRand1` | `simone.srand1` |  |
| `5:15AE` | `_SRand2` | `simone.srand_pow2` |  |
| `5:15CE` | `_SRand4` | `simone.srand_pow2` |  |
| `5:15EE` | `_SRand8` | `simone.srand_pow2` |  |
| `5:160E` | `_SRand16` | `simone.srand_pow2` |  |
| `5:162E` | `_SRand32` | `simone.srand_pow2` |  |
| `5:164E` | `_SRand64` | `simone.srand_pow2` |  |
| `5:166E` | `_SRand128` | `simone.srand_pow2` |  |
| `5:168E` | `_SRand256` | `simone.srand_pow2` |  |
| `5:26C4` | `_RIsItDirt` | `gameplay.r_is_it_dirt` |  |
| `5:56BA` | `_SGetDis` | `gameplay.s_get_dis` |  |
| `5:56DA` | `_IsValidLocation` | `gameplay.is_valid_location` |  |
| `5:5720` | `_IsYellowAnt` | `gameplay.is_yellow_ant` |  |
| `5:5AD2` | `_IsClear3x3` | `gameplay.is_clear_3x3` |  |
| `5:5B2C` | `_IsClearTile` | `gameplay.is_clear_tile` |  |
| `5:5EC8` | `_IsThisEgg` | `gameplay.is_this_egg` |  |
| `5:5EE4` | `_IsThisGrass` | `gameplay.is_this_grass` |  |
| `5:5F04` | `_IsThisFood` | `gameplay.is_this_food` |  |
| `5:5F32` | `_IsThisPebble` | `gameplay.is_this_pebble` |  |
| `5:5F64` | `_IsItNFood` | `gameplay.is_it_nfood` |  |
| `5:6040` | `_GetLife` | `gameplay.life_cell_offset` |  |
| `5:60E2` | `_GetMap` | `gameplay.map_cell_offset` |  |
| `5:94A0` | `_IsNotBarrier` | `gameplay.is_not_barrier` |  |
| `5:94C6` | `_IsNotObstacle` | `gameplay.is_not_obstacle` |  |
| `5:95C6` | `_IsItDigable` | `gameplay.is_it_digable` |  |
| `5:9784` | `_IsLessThanHole` | `gameplay.is_less_than_hole` |  |
| `5:97AA` | `_IsSamePlane` | `gameplay.is_same_plane` |  |
| `5:9B4A` | `_IsItAHole` | `gameplay.is_it_a_hole` |  |
| `5:9C02` | `_IsValidA` | `gameplay.is_valid_a` |  |
| `5:9C26` | `_IsValidB` | `gameplay.is_valid_b` |  |
| `6:2CC0` | `_IsItHole` | `gameplay.is_it_hole` |  |
| `6:2D1A` | `_IsItFood` | `gameplay.is_it_food` |  |
| `7:A668` | `_Unpack` | `lzss.decode_chunk` |  |
| `7:B033` | `_DrawChar` | `render.draw_char` |  |
| `7:C256` | `_win_IsWinOpen` | `window.win_is_win_open` |  |
| `7:C2D2` | `_win_GetObjRect` | `window.win_get_obj_rect` |  |

Special cases the adapter stage must preserve:

- `_Unpack`'s island **passes a mid-operation resume back to the original
  ASM** (entry `[B7D4] != 0`) even though the pure core (`lzss.decode_chunk`)
  models the resume state fully — the delicate two-sided-streaming path is
  deliberately left authoritative in ASM under the VM. A VM-less build has no
  such fallback; it must drive `decode_chunk`'s resume codes natively (they
  mirror the ASM's own `[B7D4]` codes 0–5).
- `_GenNestMap` is listed TWICE in `hooks._ISLANDS` (identical entries; the
  second install overwrites the first, and `EXPECTED_ISLAND_COUNT = 69` counts
  the duplicate). Harmless, but worth a one-line upstream cleanup; this
  inventory reports 68 distinct islands.
- `test_state_diff.py` stubs presentation side calls when running the ASM
  oracle (`_ZapEuMapAt` seg3:0000, `_FightBalloons` seg3:499A, balloon/sound
  routines `_EggBalloons`, `_RestBalloons`, `_myBeginSound`, `_myBeginSong`,
  `_EditMessage`, `_win_LockWin`/`_win_UnlockWin`); the recovered impls omit
  those calls entirely. The JSON does not carry a per-function stub list —
  the test seeds are the record — but any adapter-level replay comparison
  must stub the same set.

## 7. The bridge layer — how the state-view seam becomes the CPU/ABI adapter

`simant/bridge/dgroup_view.py` + `simant/native/state.py` are already the
marshalling layer the generated adapters should use, not a design to replace:

- **Named fields → offsets.** `SimAntState` (a `DgroupView`) maps source-level
  names (`rng_seed` = DGROUP:0xCBF2, `window_hwnd[slot]` = 0xBCA6, the
  map/life planes at `MAP_PLANE_BASE`/`LIFE_PLANE_BASE`) onto typed
  descriptors (`_U8/_S8/_U16/_S16/_U16Array/_Bytes/StructArray`). This module
  is the ONLY place a DGROUP offset for migrated logic is written down;
  recovered logic imports the "WHERE" from here (`gameplay` imports the plane
  bases — the sanctioned dependency direction is recovered → bridge, never
  the reverse).
- **Offsets → backend.** Three swappable backends implement `rb/wb/rw/ww`:
  `ByteBackend` (flat image at `base + off`: VM `mem.data`, a raw
  `bytearray`, or `NativeGameState.data`), `SelectorBackend` (through VM
  selector translation — the faithful hybrid-mode path), `OverlayBackend`
  (read-through, write-accumulating — returns a write-set contract for
  whole-routine transforms). The state-diff oracle exploits exactly this: the
  ASM mutates the real DGROUP, the recovered impl mutates a `ByteBackend`
  copy, and the proof is a memcmp.
- **The native target.** `NativeGameState` owns the address-space image with
  no VM: it exposes the same `.data` + `rb/rw/wb/ww` + `_xlat` surface as the
  VM's `mem` (mirroring the selector table, RPL-masked), so every recovered
  function and bridge adapter runs over it unchanged; `from_machine()` is the
  bootstrap seam (snapshot VM → owned image). A generated CPU/ABI adapter
  for, say, `_DoForageAnt` therefore reduces to: read `slot` from the
  emulated stack at `[bp+4]`, bind the three fixed views (DGROUP/seg10,
  SIMANT_DATA_GROUP/seg8, PACK/seg9) over the state image, call
  `gameplay.do_forage_ant(dgroup, simant_data_group, pack, slot)`, and emit
  the near-return exit state. The islands in hooks.py do precisely this today
  against the VM; the generated layer does it against `NativeGameState`.

Adapter-relevant subtlety: views are addressed by **real segment-relative
offsets** (`view.rw(0x8A5E)` reads that offset within the segment, not a
window-relative index) — `ByteBackend(rec, -lo)` in the tests shows the
convention. Word fields wrap at 0xFFFF like the ASM; none of the recovered
code bounds-checks plane indexing (matching the original), so the adapter
must hand it full 64 KB segment images.

## 8. Ambiguities, mismatches, and findings

**No unresolved ambiguous matches.** Every docstring-cited address resolves in
SIMANTW.SYM, no two recovered functions claim the same address, and no
docstring symbol disagrees with the SYM name at its cited address. Items that
needed judgment, and findings a routing design should know:

1. **Twin-suffix citations** (`_WindowsMono_MakeTable4x4a`/`b` seg4:442C/44B9,
   `_2x2a`/`b` seg4:4542/45DB): the docstring cites the pair; SYM names the
   `b` twin at the second address. Resolved as `derived` (one impl, two
   entries; the only behavioral difference — pair count / stride — is a
   parameter).
2. **CRT alias names**: six seg4 addresses carry two SYM names each
   (`_strcmpi`/`_stricmp`, `_remove`/`_unlink`, `__aFlmul`/`__aFulmul`,
   `__aFNalmul`/`__aFNaulmul`, `__aFftol`/`__ftol`, `__aFchkstk`/`__chkstk`).
   `crt_math.a_f_ulmul` covers BOTH `__aFulmul` and `__aFlmul` (signed and
   unsigned truncating long-multiply share code); the JSON entry is keyed
   4:096E with the alias noted. Address-keyed matching (not name-keyed) is
   the right primitive for the pipeline because of exactly this.
3. **`hooks._ISLANDS` lists `_GenNestMap` twice** (§6).
4. **`gameplay._sx32` is dead code** (§3).
5. **`do_nest_ant_b`'s docstring quotes another routine's frame**
   (`caste_sub=[bp+12]`, by analogy to `_DoDigInB`, later corrected by fresh
   disassembly in the same docstring — the real frame is
   `x=[bp+6], y=[bp+8], mode=[bp+10]`). The mechanical extractor filters
   quoted frames against the actual parameter list; this was the one place
   the filter mattered. A warning for anyone re-deriving ABI from prose:
   docstrings cite OTHER routines' offsets freely.
6. **`simone.py` documents per-function addresses in the MODULE docstring**
   (the `_SRand*` family), not per-function — the only module needing
   hand-curated address overrides in the extraction.
7. **Fail-loud divergences-by-design** live in docstrings, not the JSON:
   `is_it_yellow` (5:96B6) raises `KeyError` for `colony` outside 0..3 (the
   original reads uninitialized stack); `clip_line` bounds its bisection
   loops and raises on non-convergence; the divide helpers raise
   `ZeroDivisionError` where the ASM would `#DE`.

## 9. What surprised, and what it means for adapter routing

- **The corpus is bigger than the journal's day-to-day framing suggests**:
  309 SYM entries / 300 impls, including essentially all of SIMTWO's
  strategy + worldgen tier (68 entries in seg7) — not just the seg5/seg6 sim
  core. The authoritative manual corpus is ~23% of all code symbols, and by
  sampled runtime it is the hot sim majority.
- **Coverage is contiguous, not scattered**: seg6 is 19 symbols from closure.
  Routing can treat whole segments as authoritative-with-exceptions rather
  than checking per call: seg1/2/3 = lift, seg5/6/7 sim tier = route to
  Python, seg4 = mixed (CRT math + blitters recovered; stdio/heap/file CRT
  not).
- **One recovery ≠ one routine**: the pipeline's matcher must support
  1-impl↔N-entries (twins, up to 8), N-impls↔1-entry (`_GetLife` =
  `life_cell_offset` + `get_life_value`), and 1-entry↔0-impls-but-an-island.
  The JSON encodes all three explicitly (`twins`, `split_of`,
  `islands[].recovered_twin`).
- **The near/far split is semantic, not per-segment**: the seg6 behavior
  batch is NEAR-return (same-segment dispatcher) with `do_red_initiator` the
  lone FAR outlier — adapters cannot assume one frame shape per segment. The
  JSON `ret` field is per-entry, and 144 entries need the convention
  closed from island bodies or disassembly before adapter emission.
- **Platform effects are already factored out**: recovered impls simply omit
  presentation calls (redraw/sound/balloons), and the oracle stubs them when
  running the ASM. The VM-less layer therefore needs a presentation-effect
  sink at exactly those call sites in LIFTED code only — recovered code never
  calls out.
- **The three-view marshalling is fixed at load time** (the pointer globals
  reaching seg8/seg9 are never written by any instruction in seg1–7) —
  adapters can bind views once per state image, which makes the modal
  `(dgroup, simant_data_group, pack, slot)` signature cheap to generate.
