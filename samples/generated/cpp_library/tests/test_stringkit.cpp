// Core-feature GoogleTest suite (models the live-generated tests).
#include <gtest/gtest.h>
#include "stringkit_lib.h"

TEST(StringKitTest, ToUpper) {
  EXPECT_EQ(app::to_upper("abc"), "ABC");
  EXPECT_EQ(app::to_upper("Mixed123"), "MIXED123");
}

TEST(StringKitTest, Reverse) {
  EXPECT_EQ(app::reverse("abc"), "cba");
  EXPECT_EQ(app::reverse(""), "");
}

TEST(StringKitTest, IsPalindrome) {
  EXPECT_TRUE(app::is_palindrome("racecar"));
  EXPECT_FALSE(app::is_palindrome("hello"));
}
