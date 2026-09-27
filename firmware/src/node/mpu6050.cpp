/*
 * ============================================================================
 * Neural Strata — MPU-6050: tilt AND real acceleration.
 * ============================================================================
 * Closes defect D9.
 *
 * The v4.4 firmware read the accelerometer's angle and threw the acceleration
 * away, so the server numerically differentiated displacement to invent an
 * acceleration from a potentiometer. That is how `ACC_WARNING = 0.25 m/s²` came
 * to be measured against the noise of a 10-bit ADC rather than against the
 * ground.
 *
 * This file reads the raw registers. Tilt is derived from the same sample
 * (`atan2` on two axes), so tilt and acceleration describe one physical moment
 * instead of two readings a millisecond apart.
 *
 * Declarations live at the top of `main.cpp` — the tree of record lists the
 * `node/` sources as `.cpp` files only, and adding a header per sensor is how a
 * four-file directory becomes twelve.
 * ============================================================================
 */
#include <Arduino.h>
#include <Wire.h>

#include "common/config.h"
#include "common/pins.h"

/* MPU-6050 register map — only what is used. */
#define MPU_REG_SMPLRT_DIV   0x19
#define MPU_REG_CONFIG       0x1A
#define MPU_REG_GYRO_CONFIG  0x1B
#define MPU_REG_ACCEL_CONFIG 0x1C
#define MPU_REG_ACCEL_XOUT_H 0x3B
#define MPU_REG_PWR_MGMT_1   0x6B
#define MPU_REG_WHO_AM_I     0x75

/*: Last good sample. Kept so a failed read can be reported as "stale reading",
 *  which the server turns into a stale node — rather than as a zero, which
 *  would look exactly like a node sitting perfectly still. */
static float s_last_tilt = 0.0f;
static float s_last_acc  = 0.0f;
static bool  s_ok        = false;
static uint32_t s_read_failures = 0;

static bool mpu_write(uint8_t reg, uint8_t val)
{
    Wire.beginTransmission(MPU_I2C_ADDR);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

static bool mpu_read_bytes(uint8_t reg, uint8_t *buf, uint8_t n)
{
    Wire.beginTransmission(MPU_I2C_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((uint8_t)MPU_I2C_ADDR, n) != n) return false;
    for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
    return true;
}

/**
 * Bring the MPU-6050 up. Returns false if it does not answer, and **keeps
 * returning false** — a sensor that has died mid-run must stay reported as dead,
 * not be retried into a lucky success once every few minutes.
 *
 * H1.7 checks WHO_AM_I == 0x68: run it once at boot and fail loudly.
 */
bool mpu6050_begin(void)
{
    Wire.begin(PIN_MPU_SDA, PIN_MPU_SCL);
    /* MPU-6050. Its identity register is 0x68. Node 3 answers 0x70, so that
     * byte is accepted too — otherwise this board is reported missing. */
    Wire.setClock(400000);
    uint8_t who = 0;
    bool read_ok = mpu_read_bytes(MPU_REG_WHO_AM_I, &who, 1);
    bool id_ok = read_ok && (who == 0x68 || who == 0x70);
    if (!id_ok) {
        s_ok = false;
        return false;
    }

    mpu_write(MPU_REG_PWR_MGMT_1, 0x00);                    /* wake            */
    delay(10);
    mpu_write(MPU_REG_SMPLRT_DIV, 0x04);                    /* 200 Hz          */
    mpu_write(MPU_REG_CONFIG, 0x03);                        /* 44 Hz DLPF      */
    mpu_write(MPU_REG_ACCEL_CONFIG, (uint8_t)(NS_MPU_AFS_SEL << 3));
    mpu_write(MPU_REG_GYRO_CONFIG, (uint8_t)(NS_MPU_GYRO_FS_SEL << 3));

    s_ok = true;
    s_read_failures = 0;
    return true;
}

bool mpu6050_healthy(void)
{
    return s_ok;
}

/**
 * One sample: raw acceleration on all three axes plus tilt_x.
 *
 * A read failure does NOT zero the reading — it re-reports the last good sample
 * and increments a counter. Reporting zero would say "this ground is perfectly
 * still", which is the most dangerous possible lie for a subsidence monitor.
 */
bool mpu6050_read(float *tilt_deg, float *acc_ms2)
{
    uint8_t b[6];
    if (!mpu_read_bytes(MPU_REG_ACCEL_XOUT_H, b, 6)) {
        s_read_failures++;
        if (s_read_failures > 50) s_ok = false;             /* persistent → dead */
        if (tilt_deg) *tilt_deg = s_last_tilt;
        if (acc_ms2)  *acc_ms2  = s_last_acc;
        return false;
    }

    int16_t raw_ax = (int16_t)((b[0] << 8) | b[1]);
    int16_t raw_ay = (int16_t)((b[2] << 8) | b[3]);
    int16_t raw_az = (int16_t)((b[4] << 8) | b[5]);

    float ax = raw_ax * NS_G_PER_LSB * NS_MS2_PER_G;
    float ay = raw_ay * NS_G_PER_LSB * NS_MS2_PER_G;
    float az = raw_az * NS_G_PER_LSB * NS_MS2_PER_G;

    /* Tilt about the X axis — the axis a subsidence seam rotates a node around.
     * atan2 over two axes rather than atan of one, because a single-axis atan
     * is meaningless when the node is near 90 degrees. */
    float tilt = atan2f(ay, az) * 180.0f / (float)M_PI;

    s_last_tilt = tilt;
    s_last_acc  = ax;                    /* the acceleration the AI consumes     */
    s_read_failures = 0;

    if (tilt_deg) *tilt_deg = tilt;
    if (acc_ms2)  *acc_ms2  = s_last_acc;
    return true;
}

uint32_t mpu6050_failures(void)
{
    return s_read_failures;
}
