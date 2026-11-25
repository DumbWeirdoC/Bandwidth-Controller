package com.bandwidthcontroller.app;

import android.net.VpnService;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class LocalRateProxy {

    public static final int PROXY_PORT = 9483;

    private final VpnService vpnService;
    private volatile boolean running = false;
    private ServerSocket serverSocket;
    private final ExecutorService executor = Executors.newCachedThreadPool();

    private final RateLimiter downloadLimiter = new RateLimiter();
    private final RateLimiter uploadLimiter = new RateLimiter();

    public static class RateLimiter {
        private double rateBytesPerSec = 0;
        private double nextSlotNs = System.nanoTime();
        private final Object lock = new Object();

        public void setRateBytes(double bytesPerSec) {
            synchronized (lock) {
                this.rateBytesPerSec = Math.max(0, bytesPerSec);
                this.nextSlotNs = System.nanoTime();
            }
        }

        public int getChunkSize() {
            double r = rateBytesPerSec;
            if (r <= 0) return 32768;
            if (r < 100 * 1024) return 2048;
            if (r < 1024 * 1024) return 8192;
            return 32768;
        }

        public void consume(int bytes) {
            double waitMs = 0;
            synchronized (lock) {
                if (rateBytesPerSec <= 0 || bytes <= 0) return;
                double now = System.nanoTime();
                double maxCreditNs = 120_000_000.0; // 120ms burst credit
                if (nextSlotNs < now - maxCreditNs) {
                    nextSlotNs = now - maxCreditNs;
                }
                double durationNs = (bytes / rateBytesPerSec) * 1_000_000_000.0;
                nextSlotNs += durationNs;
                waitMs = (nextSlotNs - now) / 1_000_000.0;
            }
            if (waitMs > 1.0) {
                try {
                    Thread.sleep((long) Math.min(waitMs, 2000.0));
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
            }
        }
    }

    public LocalRateProxy(VpnService vpnService) {
        this.vpnService = vpnService;
    }

    public void setLimitKb(double limitKb) {
        double bytesPerSec = limitKb * 1024.0;
        downloadLimiter.setRateBytes(bytesPerSec);
        uploadLimiter.setRateBytes(bytesPerSec);
    }

    public void start() {
        if (running) return;
        running = true;
        executor.execute(() -> {
            try {
                serverSocket = new ServerSocket(PROXY_PORT, 64, InetAddress.getByName("127.0.0.1"));
                while (running && !serverSocket.isClosed()) {
                    Socket client = serverSocket.accept();
                    executor.execute(() -> handleClient(client));
                }
            } catch (IOException ignored) {
            }
        });
    }

    public void stop() {
        running = false;
        if (serverSocket != null) {
            try {
                serverSocket.close();
            } catch (IOException ignored) {}
            serverSocket = null;
        }
        executor.shutdownNow();
    }

    private void handleClient(Socket client) {
        Socket remote = null;
        try {
            BufferedInputStream in = new BufferedInputStream(client.getInputStream());
            OutputStream out = client.getOutputStream();

            String firstLine = readLine(in);
            if (firstLine == null || firstLine.isEmpty()) return;

            String[] parts = firstLine.split(" ");
            if (parts.length < 2) return;

            String method = parts[0].toUpperCase();
            String target = parts[1];

            if ("CONNECT".equals(method)) {
                String host = target;
                int port = 443;
                int colon = target.lastIndexOf(':');
                if (colon > 0) {
                    host = target.substring(0, colon);
                    try {
                        port = Integer.parseInt(target.substring(colon + 1));
                    } catch (NumberFormatException ignored) {}
                }

                // Consume remaining headers
                String h;
                while ((h = readLine(in)) != null && !h.isEmpty()) {}

                remote = new Socket(host, port);
                if (vpnService != null) {
                    vpnService.protect(remote);
                }

                out.write("HTTP/1.1 200 Connection Established\r\n\r\n".getBytes(StandardCharsets.US_ASCII));
                out.flush();

                final Socket rSock = remote;
                Thread upThread = new Thread(() -> pipe(in, getOut(rSock), uploadLimiter));
                upThread.start();
                pipe(getIn(rSock), out, downloadLimiter);
                upThread.join(1000);
            } else {
                ByteArrayOutputStream headerBuf = new ByteArrayOutputStream();
                headerBuf.write((firstLine + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                String host = null;
                int port = 80;
                String h;
                while ((h = readLine(in)) != null && !h.isEmpty()) {
                    headerBuf.write((h + "\r\n").getBytes(StandardCharsets.ISO_8859_1));
                    if (h.toLowerCase().startsWith("host:")) {
                        String hv = h.substring(5).trim();
                        int c = hv.lastIndexOf(':');
                        if (c > 0) {
                            host = hv.substring(0, c);
                            try {
                                port = Integer.parseInt(hv.substring(c + 1));
                            } catch (NumberFormatException ignored) {}
                        } else {
                            host = hv;
                        }
                    }
                }
                headerBuf.write("\r\n".getBytes(StandardCharsets.ISO_8859_1));

                if (host == null) return;
                remote = new Socket(host, port);
                if (vpnService != null) {
                    vpnService.protect(remote);
                }
                OutputStream rOut = remote.getOutputStream();
                rOut.write(headerBuf.toByteArray());
                rOut.flush();

                final Socket rSock = remote;
                Thread upThread = new Thread(() -> pipe(in, rOut, uploadLimiter));
                upThread.start();
                pipe(getIn(rSock), out, downloadLimiter);
                upThread.join(1000);
            }
        } catch (Exception ignored) {
        } finally {
            closeQuietly(remote);
            closeQuietly(client);
        }
    }

    private void pipe(InputStream in, OutputStream out, RateLimiter limiter) {
        if (in == null || out == null) return;
        try {
            byte[] buf = new byte[32768];
            while (running) {
                int toRead = Math.min(buf.length, limiter.getChunkSize());
                int read = in.read(buf, 0, toRead);
                if (read <= 0) break;
                limiter.consume(read);
                out.write(buf, 0, read);
                out.flush();
            }
        } catch (Exception ignored) {}
    }

    private String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        int b;
        while ((b = in.read()) != -1) {
            if (b == '\n') break;
            if (b != '\r') baos.write(b);
        }
        if (b == -1 && baos.size() == 0) return null;
        return baos.toString(StandardCharsets.ISO_8859_1.name());
    }

    private InputStream getIn(Socket s) {
        try { return s.getInputStream(); } catch (IOException e) { return null; }
    }

    private OutputStream getOut(Socket s) {
        try { return s.getOutputStream(); } catch (IOException e) { return null; }
    }

    private void closeQuietly(Socket s) {
        if (s != null) {
            try { s.close(); } catch (IOException ignored) {}
        }
    }
}
