<?php

class RoborockQrevoEdgeT extends IPSModuleStrict
{
    public function Create(): void
    {
        parent::Create();

        $this->RegisterPropertyBoolean('Enabled', true);
        $this->RegisterPropertyString('PythonPath', '/root/roborock-test/bin/python');
        $this->RegisterPropertyString('SessionFile', '/root/.cache/roborock-user-params.pkl');
        $this->RegisterPropertyString('CacheFile', '/root/.cache/roborock-cache-data.pkl');
        $this->RegisterPropertyString('DeviceName', 'Roborock Qrevo EdgeT');
        $this->RegisterPropertyInteger('PollInterval', 300);
        $this->RegisterPropertyInteger('CommandTimeout', 120);
        $this->RegisterPropertyString('ExportDirectory', '/root/roborock_export');

        $this->RegisterAttributeInteger('MapImageID', 0);

        $this->RegisterTimer('PollTimer', 0, 'RQT_Poll($_IPS[\'TARGET\']);');

        $this->RegisterVariableBoolean('Connected', 'Roborock verbunden');
        $this->RegisterVariableInteger('Battery', 'Batterie (%)');
        $this->RegisterVariableString('State', 'Status');
        $this->RegisterVariableString('ErrorCode', 'Fehlercode');
        $this->RegisterVariableInteger('CleaningProgress', 'Reinigungsfortschritt (%)');
        $this->RegisterVariableFloat('CleaningArea', 'Reinigungsfläche (m²)');
        $this->RegisterVariableInteger('CleaningTime', 'Reinigungsdauer (Gerätewert)');
        $this->RegisterVariableBoolean('Charging', 'Lädt');
        $this->RegisterVariableInteger('CurrentMap', 'Aktuelle Karte');
        $this->RegisterVariableInteger('CurrentRoom', 'Aktuelles Raumsegment');
        $this->RegisterVariableString('CurrentRoomName', 'Aktueller Raum');
        $this->RegisterVariableInteger('VacuumX', 'Roboterposition X');
        $this->RegisterVariableInteger('VacuumY', 'Roboterposition Y');
        $this->RegisterVariableInteger('VacuumAngle', 'Roboterwinkel');
        $this->RegisterVariableInteger('LastUpdate', 'Letzte erfolgreiche Aktualisierung', '~UnixTimestamp');
        $this->RegisterVariableString('LastResult', 'Letzte Meldung');
        $this->RegisterVariableString('RoomsJSON', 'Räume (JSON)');
        $this->RegisterVariableString('MapJSON', 'Kartendaten (JSON)');

        $this->RegisterVariableBoolean('RefreshNow', 'Jetzt aktualisieren');
        $this->EnableAction('RefreshNow');
        $this->RegisterVariableBoolean('StartCleaning', 'Reinigung starten');
        $this->EnableAction('StartCleaning');
        $this->RegisterVariableBoolean('PauseCleaning', 'Reinigung pausieren');
        $this->EnableAction('PauseCleaning');
        $this->RegisterVariableBoolean('StopCleaning', 'Reinigung stoppen');
        $this->EnableAction('StopCleaning');
        $this->RegisterVariableBoolean('ReturnToDock', 'Zur Ladestation fahren');
        $this->EnableAction('ReturnToDock');

        foreach (['RefreshNow', 'StartCleaning', 'PauseCleaning', 'StopCleaning', 'ReturnToDock'] as $ident) {
            $this->SetValue($ident, false);
        }
        $this->SetValue('Connected', false);
        $this->SetValue('LastResult', 'Noch keine Verbindung hergestellt.');
    }

    public function ApplyChanges(): void
    {
        parent::ApplyChanges();

        if (!$this->ReadPropertyBoolean('Enabled')) {
            $this->SetTimerInterval('PollTimer', 0);
            $this->SetStatus(104); // Inaktiv
            return;
        }

        $this->SetStatus(102); // Aktiv; die Verbindung wird separat in Connected angezeigt.
        // Erste Aktualisierung kurz nach dem Aktivieren; Poll() stellt danach den regulären Takt ein.
        $this->SetTimerInterval('PollTimer', 10000);
        $this->EnsureMapMedia();
    }

    public function RequestAction(string $Ident, mixed $Value): void
    {
        if (!$Value) {
            $this->SetValue($Ident, false);
            return;
        }

        try {
            switch ($Ident) {
                case 'RefreshNow':
                    $this->RefreshNow();
                    break;
                case 'StartCleaning':
                    $this->StartCleaning();
                    break;
                case 'PauseCleaning':
                    $this->PauseCleaning();
                    break;
                case 'StopCleaning':
                    $this->StopCleaning();
                    break;
                case 'ReturnToDock':
                    $this->ReturnToDock();
                    break;
                default:
                    throw new Exception('Unbekannte Aktion: ' . $Ident);
            }
        } finally {
            if (in_array($Ident, ['RefreshNow', 'StartCleaning', 'PauseCleaning', 'StopCleaning', 'ReturnToDock'], true)) {
                $this->SetValue($Ident, false);
            }
        }
    }

    /** Regelmäßiger Timer-Aufruf. */
    public function Poll(): bool
    {
        if (!$this->ReadPropertyBoolean('Enabled')) {
            $this->SetTimerInterval('PollTimer', 0);
            return false;
        }

        try {
            $result = $this->ExecuteBridge(['action' => 'status']);
            $this->ApplySnapshot($result);
            $this->SetValue('Connected', true);
            $this->SetValue('LastUpdate', time());
            $this->SetValue('LastResult', 'Daten erfolgreich aktualisiert.');
            $this->SendDebug('Poll', 'Status und Karte aktualisiert.', 0);
            return true;
        } catch (Throwable $e) {
            $this->SetValue('Connected', false);
            $this->SetValue('LastResult', $e->getMessage());
            $this->SendDebug('Poll Fehler', $e->getMessage(), 0);
            IPS_LogMessage('Roborock Qrevo EdgeT', $e->getMessage());
            return false;
        } finally {
            $interval = max(60, min(3600, $this->ReadPropertyInteger('PollInterval')));
            $this->SetTimerInterval('PollTimer', $this->ReadPropertyBoolean('Enabled') ? $interval * 1000 : 0);
        }
    }

    public function RefreshNow(): bool
    {
        return $this->Poll();
    }

    public function TestConnection(): bool
    {
        $ok = $this->RefreshNow();
        if ($ok) {
            IPS_LogMessage('Roborock Qrevo EdgeT', 'Verbindungstest erfolgreich.');
        }
        return $ok;
    }

    public function StartCleaning(): bool
    {
        return $this->SendVacuumCommand('start');
    }

    public function PauseCleaning(): bool
    {
        return $this->SendVacuumCommand('pause');
    }

    public function StopCleaning(): bool
    {
        return $this->SendVacuumCommand('stop');
    }

    public function ReturnToDock(): bool
    {
        return $this->SendVacuumCommand('dock');
    }

    /**
     * Reinigt ein einzelnes Raumsegment.
     * Nur Segmente mit bekannten Raum-Metadaten und IoT-ID sind erlaubt.
     * Von PHP-Skripten aufrufbar: RQT_CleanRoom($instanceID, 1, 1)
     */
    public function CleanRoom(int $segmentId, int $repeat = 1): bool
    {
        if ($segmentId < 1) {
            $this->SetValue('LastResult', 'Raumreinigung abgebrochen: Segment-ID muss positiv sein.');
            return false;
        }
        if ($repeat < 1 || $repeat > 3) {
            $this->SetValue('LastResult', 'Raumreinigung abgebrochen: Wiederholung muss zwischen 1 und 3 liegen.');
            return false;
        }

        $mapData = json_decode($this->GetValue('MapJSON'), true);
        if (!is_array($mapData) || !isset($mapData['rooms']) || !is_array($mapData['rooms'])) {
            $this->SetValue('LastResult', 'Bitte zuerst eine erfolgreiche Kartenaktualisierung durchführen.');
            return false;
        }

        $allowedRoom = null;
        foreach ($mapData['rooms'] as $room) {
            if ((int) ($room['segment_id'] ?? -1) === $segmentId) {
                $allowedRoom = $room;
                break;
            }
        }

        if (!is_array($allowedRoom) || empty($allowedRoom['controllable']) || empty($allowedRoom['iot_id'])) {
            $this->SetValue('LastResult', 'Segment ' . $segmentId . ' ist nicht zugeordnet und darf nicht gereinigt werden.');
            return false;
        }

        try {
            $result = $this->ExecuteBridge([
                'action' => 'command',
                'command' => 'clean_room',
                'segment_id' => $segmentId,
                'repeat' => $repeat
            ]);
            $roomName = (string) ($result['room_name'] ?? $allowedRoom['name'] ?? ('Segment ' . $segmentId));
            $this->SetValue('LastResult', 'Raumreinigung an Roborock übermittelt: ' . $roomName . '.');
            $this->ScheduleFastPoll();
            return true;
        } catch (Throwable $e) {
            $this->SetValue('LastResult', $e->getMessage());
            IPS_LogMessage('Roborock Qrevo EdgeT', $e->getMessage());
            return false;
        }
    }

    /** Liefert die zuletzt empfangenen Räume als JSON. */
    public function GetRoomsJSON(): string
    {
        return $this->GetValue('RoomsJSON');
    }

    /** Liefert die zuletzt empfangenen Kartendaten als JSON. */
    public function GetMapJSON(): string
    {
        return $this->GetValue('MapJSON');
    }

    private function SendVacuumCommand(string $command): bool
    {
        try {
            $result = $this->ExecuteBridge(['action' => 'command', 'command' => $command]);
            $labels = [
                'start' => 'Startbefehl',
                'pause' => 'Pausenbefehl',
                'stop' => 'Stoppbefehl',
                'dock' => 'Rückkehr zur Ladestation'
            ];
            $this->SetValue('LastResult', ($labels[$command] ?? $command) . ' an Roborock übermittelt.');
            $this->ScheduleFastPoll();
            return true;
        } catch (Throwable $e) {
            $this->SetValue('LastResult', $e->getMessage());
            IPS_LogMessage('Roborock Qrevo EdgeT', $e->getMessage());
            return false;
        }
    }

    private function ScheduleFastPoll(): void
    {
        if ($this->ReadPropertyBoolean('Enabled')) {
            $this->SetTimerInterval('PollTimer', 3000);
        }
    }

    private function ApplySnapshot(array $result): void
    {
        $status = $result['status'] ?? [];
        $mapExport = $result['map_export'] ?? [];
        $mapData = $mapExport['map_data'] ?? [];

        $this->SetValue('Battery', is_numeric($status['battery'] ?? null) ? (int) $status['battery'] : 0);
        $this->SetValue('State', (string) ($status['state'] ?? 'unbekannt'));
        $this->SetValue('ErrorCode', (string) ($status['error_code'] ?? 'unbekannt'));
        $this->SetValue('CleaningProgress', is_numeric($status['clean_percent'] ?? null) ? (int) $status['clean_percent'] : 0);
        $this->SetValue('CleaningArea', is_numeric($status['clean_area_m2'] ?? null) ? (float) $status['clean_area_m2'] : 0.0);
        $this->SetValue('CleaningTime', is_numeric($status['clean_time'] ?? null) ? (int) $status['clean_time'] : 0);
        $this->SetValue('Charging', (bool) ($status['charging'] ?? false));
        $this->SetValue('CurrentMap', is_numeric($status['current_map'] ?? null) ? (int) $status['current_map'] : -1);
        $this->SetValue('CurrentRoom', is_numeric($status['current_room'] ?? null) ? (int) $status['current_room'] : 0);

        $rooms = $mapData['rooms'] ?? [];
        $currentRoomName = $status['current_room_name'] ?? null;
        if (!$currentRoomName && is_array($rooms)) {
            foreach ($rooms as $room) {
                if ((int) ($room['segment_id'] ?? -1) === (int) ($status['current_room'] ?? -999)) {
                    $currentRoomName = $room['name'] ?? null;
                    break;
                }
            }
        }
        $this->SetValue('CurrentRoomName', (string) ($currentRoomName ?? 'unbekannt'));

        $position = $status['vacuum_position'] ?? [];
        $this->SetValue('VacuumX', is_numeric($position['x'] ?? null) ? (int) $position['x'] : 0);
        $this->SetValue('VacuumY', is_numeric($position['y'] ?? null) ? (int) $position['y'] : 0);
        $this->SetValue('VacuumAngle', is_numeric($position['a'] ?? null) ? (int) $position['a'] : 0);

        $this->SetValue('RoomsJSON', json_encode(is_array($rooms) ? $rooms : [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
        $this->SetValue('MapJSON', json_encode(is_array($mapData) ? $mapData : [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

        $image = $result['map_png_base64'] ?? '';
        if (is_string($image) && $image !== '') {
            $decoded = base64_decode($image, true);
            if ($decoded !== false && strncmp($decoded, "\x89PNG\r\n\x1a\n", 8) === 0) {
                $mediaId = $this->EnsureMapMedia();
                if ($mediaId > 0) {
                    if (!IPS_SetMediaContent($mediaId, $image)) {
                        throw new Exception('IP-Symcon konnte den Karteninhalt nicht speichern.');
                    }
                    IPS_SendMediaEvent($mediaId);
                }
            } else {
                throw new Exception('Die Brücke lieferte kein gültiges PNG-Kartenbild.');
            }
        }
    }

    private function EnsureMapMedia(): int
    {
        $mediaId = @IPS_GetObjectIDByIdent('MapImage', $this->InstanceID);
        if ($mediaId === false) {
            $mediaId = IPS_CreateMedia(1); // MEDIATYPE_IMAGE
            IPS_SetParent($mediaId, $this->InstanceID);
            IPS_SetIdent($mediaId, 'MapImage');
            IPS_SetName($mediaId, 'Roborock-Karte');
            IPS_SetMediaFile($mediaId, 'RoborockMap_' . $this->InstanceID . '.png', false);
            IPS_SetMediaCached($mediaId, true);
        }
        $this->WriteAttributeInteger('MapImageID', (int) $mediaId);
        return (int) $mediaId;
    }

    private function ExecuteBridge(array $payload): array
    {
        $python = trim($this->ReadPropertyString('PythonPath'));
        $bridge = __DIR__ . DIRECTORY_SEPARATOR . 'scripts' . DIRECTORY_SEPARATOR . 'roborock_bridge.py';
        $sessionFile = trim($this->ReadPropertyString('SessionFile'));
        $cacheFile = trim($this->ReadPropertyString('CacheFile'));
        $deviceName = trim($this->ReadPropertyString('DeviceName'));
        $exportDirectory = trim($this->ReadPropertyString('ExportDirectory'));

        if ($python === '' || !is_executable($python)) {
            throw new Exception('Python ist nicht ausführbar. PythonPath prüfen: ' . $python);
        }
        if (!is_file($bridge)) {
            throw new Exception('Python-Brücke fehlt: ' . $bridge);
        }
        if ($sessionFile === '' || $cacheFile === '' || $deviceName === '' || $exportDirectory === '') {
            throw new Exception('SessionFile, CacheFile, DeviceName und ExportDirectory müssen ausgefüllt sein.');
        }
        if (!function_exists('proc_open')) {
            throw new Exception('PHP-Funktion proc_open ist deaktiviert. Bitte IP-Symcon/PHP-Konfiguration prüfen.');
        }

        $command = [
            $python,
            $bridge,
            '--session-file', $sessionFile,
            '--cache-file', $cacheFile,
            '--device-name', $deviceName,
            '--export-dir', $exportDirectory
        ];
        $descriptors = [
            0 => ['pipe', 'r'],
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w']
        ];
        $pipes = [];
        $process = proc_open($command, $descriptors, $pipes, null, null, ['bypass_shell' => true]);
        if (!is_resource($process)) {
            throw new Exception('Python-Brücke konnte nicht gestartet werden.');
        }

        $timeout = max(30, min(300, $this->ReadPropertyInteger('CommandTimeout')));
        $stdout = '';
        $stderr = '';
        $exitCode = -1;
        try {
            $input = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
            if ($input === false) {
                throw new Exception('Anfrage an Python konnte nicht als JSON codiert werden.');
            }
            fwrite($pipes[0], $input);
            fclose($pipes[0]);
            stream_set_blocking($pipes[1], false);
            stream_set_blocking($pipes[2], false);
            $deadline = microtime(true) + $timeout;
            $processStatus = null;

            while (true) {
                $chunk = stream_get_contents($pipes[1]);
                if (is_string($chunk)) {
                    $stdout .= $chunk;
                }
                $chunk = stream_get_contents($pipes[2]);
                if (is_string($chunk)) {
                    $stderr .= $chunk;
                }

                $processStatus = proc_get_status($process);
                if (!$processStatus['running']) {
                    $chunk = stream_get_contents($pipes[1]);
                    if (is_string($chunk)) $stdout .= $chunk;
                    $chunk = stream_get_contents($pipes[2]);
                    if (is_string($chunk)) $stderr .= $chunk;
                    $exitCode = (int) $processStatus['exitcode'];
                    break;
                }

                if (microtime(true) >= $deadline) {
                    proc_terminate($process, 9);
                    throw new Exception('Zeitüberschreitung der Python-Brücke nach ' . $timeout . ' Sekunden.');
                }
                usleep(100000);
            }
        } catch (Throwable $e) {
            if (is_resource($process)) {
                @proc_terminate($process, 9);
            }
            foreach ($pipes as $pipe) {
                if (is_resource($pipe)) @fclose($pipe);
            }
            @proc_close($process);
            throw $e;
        }

        foreach ([1, 2] as $index) {
            if (isset($pipes[$index]) && is_resource($pipes[$index])) {
                fclose($pipes[$index]);
            }
        }
        $closeCode = proc_close($process);
        if ($exitCode < 0 && $closeCode >= 0) {
            $exitCode = $closeCode;
        }

        $decoded = json_decode(trim($stdout), true);
        if (!is_array($decoded)) {
            $extra = trim($stderr);
            throw new Exception('Ungültige Antwort der Python-Brücke.' . ($extra !== '' ? ' ' . $extra : ''));
        }
        if (empty($decoded['ok'])) {
            $message = (string) ($decoded['error'] ?? 'Unbekannter Fehler der Python-Brücke.');
            if (trim($stderr) !== '') {
                $this->SendDebug('Python stderr', trim($stderr), 0);
            }
            throw new Exception($message);
        }
        if ($exitCode !== 0) {
            throw new Exception('Python-Brücke wurde mit Exitcode ' . $exitCode . ' beendet. ' . trim($stderr));
        }

        if (trim($stderr) !== '') {
            $this->SendDebug('Python stderr', trim($stderr), 0);
        }
        return $decoded;
    }
}
