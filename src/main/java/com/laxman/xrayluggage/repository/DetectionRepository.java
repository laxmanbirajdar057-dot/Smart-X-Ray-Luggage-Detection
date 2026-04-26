package com.laxman.xrayluggage.repository;

import java.time.LocalDateTime;
import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import com.laxman.xrayluggage.model.Detection;

// JpaRepository<Detection, Long> gives you free CRUD methods:
// save(), findById(), findAll(), deleteById(), count(), etc.
// Replaces: all manual SQLAlchemy session queries for detections
@Repository
public interface DetectionRepository extends JpaRepository<Detection, Long> {

    // Spring generates SQL automatically from method name:
    // "findByClassName" → SELECT * FROM detections WHERE class_name = ?
    List<Detection> findByClassName(String className);

    // Replaces any date-range queries from routes/detections.py
    List<Detection> findByTimestampBetween(LocalDateTime start, LocalDateTime end);

    // Custom JPQL query for paginated recent detections
    @Query("SELECT d FROM Detection d ORDER BY d.timestamp DESC")
    List<Detection> findAllOrderByTimestampDesc();
}
