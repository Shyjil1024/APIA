// Core-feature GoogleTest suite (models the live-generated tests).
#include <gtest/gtest.h>
#include "calc_console_lib.h"

TEST(CalcTest, Adds) {
  EXPECT_EQ(app::add(2, 3), 5);
  EXPECT_EQ(app::add(-1, 1), 0);
}

TEST(CalcTest, Subtracts) {
  EXPECT_EQ(app::subtract(10, 4), 6);
  EXPECT_EQ(app::subtract(0, 5), -5);
}
