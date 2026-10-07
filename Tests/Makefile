# tests/Makefile
CC = gcc
CFLAGS = -Wall -Wextra -std=c99 -g
TARGET = test_time
SOURCE = test_time.c

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) -o $(TARGET) $(SOURCE)

run: $(TARGET)
	./$(TARGET)

clean:
	rm -rf $(TARGET) $(TARGET).dSYM

.PHONY: run clean