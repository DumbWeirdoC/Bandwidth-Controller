package com.bandwidthcontroller.app;

import android.content.Intent;
import android.net.VpnService;
import android.os.ParcelFileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;

public class BandwidthVpnService extends VpnService implements Runnable {

    public static final String ACTION_STOP = "com.bandwidthcontroller.app.STOP";
    private Thread thread;
    private ParcelFileDescriptor vpnInterface = null;
    private double limitBytesPerSec = 0;
    private volatile boolean isRunning = false;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopVpn();
            return START_NOT_STICKY;
        }

        if (intent != null) {
            double limitKb = intent.getDoubleExtra("LIMIT_KB", 0);
            this.limitBytesPerSec = limitKb * 1024.0;
        }

        if (thread == null || !thread.isAlive()) {
            isRunning = true;
            thread = new Thread(this, "BandwidthVpnThread");
            thread.start();
        }

        return START_STICKY;
    }

    private void stopVpn() {
        isRunning = false;
        if (thread != null) {
            thread.interrupt();
            thread = null;
        }
        if (vpnInterface != null) {
            try {
                vpnInterface.close();
            } catch (IOException e) {
                e.printStackTrace();
            }
            vpnInterface = null;
        }
        stopSelf();
    }

    @Override
    public void run() {
        try {
            Builder builder = new Builder();
            builder.setSession("BandwidthController");
            builder.addAddress("10.0.0.2", 32);
            builder.addRoute("0.0.0.0", 0);
            builder.setMtu(1500);

            vpnInterface = builder.establish();
            if (vpnInterface == null) return;

            FileInputStream in = new FileInputStream(vpnInterface.getFileDescriptor());
            FileOutputStream out = new FileOutputStream(vpnInterface.getFileDescriptor());
            byte[] packet = new byte[32767];

            long tokenBucket = (long) limitBytesPerSec;
            long lastTokenUpdate = System.currentTimeMillis();

            while (isRunning && !Thread.currentThread().isInterrupted()) {
                int length = in.read(packet);
                if (length > 0) {
                    if (limitBytesPerSec > 0) {
                        long now = System.currentTimeMillis();
                        long elapsed = now - lastTokenUpdate;
                        if (elapsed > 0) {
                            tokenBucket = Math.min((long) limitBytesPerSec, tokenBucket + (long) (limitBytesPerSec * (elapsed / 1000.0)));
                            lastTokenUpdate = now;
                        }

                        if (tokenBucket < length) {
                            long neededTokens = length - tokenBucket;
                            long waitMs = (long) ((neededTokens / limitBytesPerSec) * 1000.0);
                            if (waitMs > 0 && waitMs < 1000) {
                                Thread.sleep(waitMs);
                            }
                            tokenBucket = 0;
                        } else {
                            tokenBucket -= length;
                        }
                    }
                    out.write(packet, 0, length);
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            stopVpn();
        }
    }

    @Override
    public void onDestroy() {
        stopVpn();
        super.onDestroy();
    }
}
