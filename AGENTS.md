# FlySight 2 Firmware - Agent Guidelines

## Build Commands
- **Full build**: Use STM32CubeIDE or run `make` in Debug/Release configurations
- **Clean build**: `make clean` then `make`
- **Pre-build**: `./prebuild.sh` (generates version.h from git tags)

## Test Commands
- **Run all tests**: `cd Tests && make run`
- **Run single test**: `cd Tests && gcc -Wall -Wextra -std=c99 -g -o test_time test_time.c && ./test_time`
- **Test framework**: Custom macros (TEST, ASSERT_EQ) with assert.h

## Code Style Guidelines

### Naming Conventions
- **Functions**: snake_case (e.g., `normalizeGNSSTime`, `FS_Common_GetRandomBytes`)
- **Variables**: snake_case (e.g., `epoch_seconds`, `busy`)
- **Types/Enums**: PascalCase (e.g., `Mode_t`, `Operation_t`, `FS_GNSS_Data_t`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `ONE_HOUR`, `TIMEOUT`)
- **Files**: snake_case.c/.h (e.g., `sensor.c`, `time.h`)

### Formatting
- **Indentation**: 4 spaces (no tabs)
- **Braces**: K&R style, opening brace on same line
- **Line length**: No strict limit, break long lines logically
- **Includes**: System includes first, then local headers alphabetically

### Structure
- **Header guards**: `#ifndef NAME_H_ #define NAME_H_ ... #endif /* NAME_H_ */`
- **Copyright**: GPL v3 header in all files
- **Enums**: `typedef enum { ... } Name_t;`
- **Static variables**: Use for module-private state
- **Error handling**: Return error codes or use assertions in tests

### Best Practices
- **Types**: Use stdint.h types (uint32_t, int8_t, etc.)
- **Memory**: Careful with embedded constraints, avoid dynamic allocation
- **Threading**: Use STM32 sequencer for task management
- **Documentation**: Doxygen-style comments for public APIs