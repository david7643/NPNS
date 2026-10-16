#pragma once
class Servo {
 public:
  void attach(int) { attached = true; }
  void detach() { attached = false; }
  void write(int a) { angle = a; writes++; }
  bool attached = false;
  int angle = -1;
  int writes = 0;
};
